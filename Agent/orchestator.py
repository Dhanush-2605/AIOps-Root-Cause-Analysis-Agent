from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from agent_state import AgentState, RemediationPlan, CriticReview,K8sAction
from agent_tools import fetch_filtered_pod_logs, get_pod_status
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.graph import StateGraph, END
from datetime import datetime, timezone
from agent_tools import get_gke_client
from kubernetes.stream import stream


# import datetime
from langgraph.types import interrupt
from langgraph.checkpoint.memory import MemorySaver

from github_utils import commit_post_mortem_to_repo
import json
from model import llm



tools = [fetch_filtered_pod_logs, get_pod_status]
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)



# SOP_CONTEXT = """

# SYSTEM ARCHITECTURE & SOPs:
# - The system is a Kubernetes cluster running multiple microservices.
# -Right now these 3 services are running they are NOT connected to each therlogger-error ,logger-info ,nginx-app



# """
SOP_CONTEXT="""
"""
INVESTIGATOR_SYS_MSG = SystemMessage(
    content=(
        f"You are an Elite Kubernetes SRE Investigator.\n{SOP_CONTEXT}\n"
        "Your ONLY goal is to gather logs and metrics using tools to identify the root cause.\n"
        "RULES:\n"
        "1. Do NOT propose fixes or kubectl remediation commands.\n"
        "2. Focus ONLY on the pod mentioned in the alert.\n"
        "3. Once you understand the issue, stop calling tools."
    )
)


# apps_v1_api=get_gke_client()
# print(apps_v1_api)
def investigator_node(state: AgentState):
    """Analyzes the alert and uses tools to gather logs/metrics."""
    messages = list(state.get("messages", []))
    
    # 1. First run: Initialize with the alert payload
    if not messages:
        alert_data = state.get("alert_payload", {})
        alert_text = (
            "Prometheus Alert Triggered:\n"
            f"```json\n{json.dumps(alert_data, indent=2)}\n```\n"
            "Please investigate this alert using your available tools."
        )
        human_msg = HumanMessage(content=alert_text)
        response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG, human_msg])
        
        # Return initial message, response, and seed the context_gathered
        return {
            "messages": [human_msg, response],
            "context_gathered": f"Started investigation for alert: {alert_data.get('alert_name')}"
        }
    
    # 2. Subsequent runs: Invoke LLM with existing history
    response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG] + messages)
    
    # Optional: If the model generated text explaining a finding, append it to context_gathered
    new_context = ""
    if response.content:
        new_context = f"\n[Investigation Finding]: {response.content}"

    return {
        "messages": [response],
        "context_gathered": new_context
    }

def proposer_node(state: AgentState):
    """Generates or adjusts a remediation plan based on context and critic/human feedback."""
    context = state.get("context_gathered", "")
    alert = state.get("alert_payload", {})
    feedback = state.get("critic_feedback", "")
    is_safe = state.get("is_safe", True)
    
    structured_llm = llm.with_structured_output(RemediationPlan)
    
    sys_msg = SystemMessage(
        content=(
            "You are an SRE Remediation Expert.\n"
            "Propose a structured JSON RemediationPlan containing valid K8sAction objects.\n"
            "Valid action_types: 'delete_pod', 'scale_deployment', 'restart_deployment', "
            "'increase_memory_limit', 'increase_cpu_limit', 'kill_idle_db_connections', "
            "'kill_container_process', 'abort_hanging_requests', 'trip_circuit_breaker', 'clear_app_cache'.\n"
            "CRITICAL ANTI-HALLUCINATION RULE: If the logs do not reveal a clear root cause, DO NOT guess. Return an empty array.\n"
            "ABSOLUTE OVERRIDE RULE: If the feedback contains 'HUMAN OPERATOR OVERRIDE', you MUST discard your previous "
            "analysis and strictly format the human's exact requested action into the appropriate K8sAction object."
        )
    )
    
    # Handle both Critic rejections AND Human-In-The-Loop feedback
    feedback_prompt = ""
    if not is_safe and feedback:
        feedback_prompt = f"\n\n⚠️ PREVIOUS PLAN REJECTED:\n{feedback}\nGenerate a NEW plan strictly addressing this feedback."

    prompt = (
        f"Alert:\n{json.dumps(alert, indent=2)}\n\n"
        f"Investigation Context:\n{context}"
        f"{feedback_prompt}\n\n"
        "Propose a safe remediation plan."
    )
    
    plan: RemediationPlan = structured_llm.invoke([sys_msg, HumanMessage(content=prompt)])
    
    if not plan.proposed_actions:
        print("   └─ Proposed Plan: [EMPTY - No safe action identified]")
    else:
        action_list = [f"{a.action_type}({getattr(a, 'target_resource', getattr(a, 'target_name', 'Unknown'))})" for a in plan.proposed_actions]
        print(f"   └─ Proposed Plan: {action_list}")
        
    return {"proposed_plan": plan}

CRITIC_SYS_MSG = SystemMessage(
    content="You are a strict Kubernetes Production Gatekeeper and Safety Critic. Review proposed commands against cluster SOPs."
)

def critic_node(state: AgentState):
    """Reviews the proposed plan against safety rules before execution."""
    
    plan = state.get("proposed_plan")
    
    # ---------------------------------------------------------
    # FIX: Fast-Path for Empty Plans
    # If the Proposer refused to guess and returned no actions,
    # skip the LLM call entirely so Groq doesn't crash.
    # ---------------------------------------------------------
    raw_actions = getattr(plan, "proposed_actions", []) if plan else []
    if not raw_actions:
        print("   └─ [CRITIC] Bypassing safety review. Plan is empty.")
        return {
            "is_safe": True, # Treat as "safe to proceed" to the executor
            "critic_feedback": "No actions proposed."
        }

    # ---------------------------------------------------------
    # Standard LLM logic continues for actual plans...
    # ---------------------------------------------------------
    structured_llm = llm.with_structured_output(CriticReview)
    
    prompt = f"""
    Review this remediation plan for safety:
    {plan.json() if hasattr(plan, 'json') else plan}
    
    Ensure it does not violate core SOPs. If unsafe, provide specific feedback.
    """
    
    # The system message forcing the tool usage
    sys_msg = SystemMessage(
        content=(
            "You are a strict Kubernetes Safety Critic. You MUST use the CriticReview tool "
            "to output your decision. Evaluate the plan and return a valid JSON object."
        )
    )
    
    try:
        review: CriticReview = structured_llm.invoke([sys_msg, HumanMessage(content=prompt)])
        
        status = "✅ APPROVED" if review.is_safe else "❌ REJECTED"
        print(f"   └─ Critic Decision: {status}")
        if not review.is_safe:
            print(f"      └─ Reason: {review.feedback}")
            
        return {
            "is_safe": review.is_safe,
            "critic_feedback": review.feedback
        }
        
    except Exception as e:
        # Fallback in case Groq flakes again, ensuring the graph fails safely
        print(f"   └─ ⚠️ [CRITIC ERROR] LLM failed to parse. Rejecting for safety. {e}")
        return {
            "is_safe": False,
            "critic_feedback": "Critic LLM crashed or failed to output structured JSON."
        }
        
        

from kubernetes import client, config
from langgraph.types import interrupt

def get_action_attr(action, key: str, default=None):
    """Safely reads attributes whether 'action' is a Pydantic object or a dict after deserialization."""
    if isinstance(action, dict):
        return action.get(key, default)
    return getattr(action, key, default)


def executor_node(state: AgentState):
    """Executes actions via K8s SDK with HITL approval and safe checkpoint deserialization."""
    # Initialize Kubernetes SDK Clients inside node
    
    v1_api, apps_v1_api = get_gke_client()

    plan = state.get("proposed_plan")
    is_safe = state.get("is_safe", True)
    critic_feedback = state.get("critic_feedback", "")
    
    # 1. Safely extract actions
    raw_actions = get_action_attr(plan, "proposed_actions", []) if plan else []
    if not raw_actions:
        print("\n⚠️ AI proposed no safe actions. Moving to post-mortem.")
        return {"execution_logs": "[NO ACTION] AI determined no safe action could be taken.", "hitl_route": "abort"}

    # 2. Format the ENTIRE plan for a single Human-in-the-Loop review
    action_summaries = []
    for action in raw_actions:
        act_type = get_action_attr(action, "action_type")
        # Check both target_resource (new schema) and target_name (old schema) just in case
        tgt = get_action_attr(action, "target_name") or get_action_attr(action, "target_name", "Unknown")
        ns = get_action_attr(action, "namespace", "default")
        val = get_action_attr(action, "value")
        
        summary = f"- {act_type} on {tgt} (namespace: {ns})"
        if val:
            summary += f" with value: {val}"
        action_summaries.append(summary)

    summary_text = "\n".join(action_summaries)
    
    warning_banner = ""
    if not is_safe:
        warning_banner = f"\n⚠️ CRITIC SAFETY WARNING: {critic_feedback}\n"

    # 3. Trigger ONE interrupt for the whole plan
    user_response = interrupt({
        "is_safe": is_safe,
        "warning": warning_banner,
        "question": f"{warning_banner}Proposed Plan to Execute:\n{summary_text}\n\nHow do you want to proceed? (approve / abort / feedback)"
    })

    # 4. Process the complex HITL Decision
    # Handle both dictionary response (from our new FastAPI setup) or simple string
    if isinstance(user_response, dict):
        decision = user_response.get("decision", "abort").lower()
        human_feedback = user_response.get("feedback", "")
    else:
        decision = "approve" if str(user_response).strip().lower() in ["y", "yes"] else "abort"
        human_feedback = ""

    if decision == "feedback":
        print(f"\n🔄 Routing back to Proposer with human feedback: {human_feedback}")
        return {
            "critic_feedback": f"HUMAN OPERATOR OVERRIDE: {human_feedback}",
            "is_safe": False, 
            "hitl_route": "replan"
        }
    
    elif decision == "abort":
        print("\n❌ Execution aborted by human. Generating incident report...")
        return {
            "execution_logs": "[ABORTED BY HUMAN OPERATOR] No actions executed.",
            "hitl_route": "abort"
        }

    # 5. EXECUTION LOOP (If Approved)
    print("\n🚀 [EXECUTING K8S SDK]...")
    output_logs = ""
    
    for action in raw_actions:
        action_type = get_action_attr(action, "action_type")
        target_name = get_action_attr(action, "target_name")
        namespace = get_action_attr(action, "namespace", "default")
        val = get_action_attr(action, "value")

        # action_summary = f"{action_type} on {target_name} (namespace: {namespace})"
        # if val:
        #     action_summary += f" with value: {val}"

        # warning_banner = ""
        # if not is_safe:
        #     warning_banner = f"\n⚠️ CRITIC SAFETY WARNING: {critic_feedback}\n"

        # # Trigger Human-in-the-Loop Pause
        # user_response = interrupt({
        #     "is_safe": is_safe,
        #     "warning": warning_banner,
        #     "question": f"{warning_banner}Action to execute: {action_summary}\nApprove action? (y/n): "
        # })

        # # Process User Input after resume
        # if str(user_response).strip().lower() not in ["y", "yes"]:
        #     output_logs += f"\n[ABORTED BY USER] Execution canceled for: {action_summary}"
        #     print(f"\n❌ Execution aborted by user for target: {target_name}")
        #     break

        # print(f"\n🚀 [EXECUTING K8S SDK] Calling API for: {action_summary}...")

        try:
            # ---------------------------------------------------------
            # 1. INFRASTRUCTURE & POD OPERATIONS 
            # ---------------------------------------------------------
            if action_type == "delete_pod":
                v1_api.delete_namespaced_pod(name=target_name, namespace=namespace)
                output_logs += f"\n[SUCCESS] Deleted pod '{target_name}' in '{namespace}'"
                print(f"   └─ ✅ Pod deleted successfully.")

            elif action_type == "restart_deployment":
                now = datetime.now().isoformat() + "Z"
                body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
                apps_v1_api.patch_namespaced_deployment(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Restarted deployment '{target_name}' in '{namespace}'"
                print(f"   └─ ✅ Deployment restart triggered.")

            elif action_type == "scale_deployment":
                body = {"spec": {"replicas": int(val)}}
                apps_v1_api.patch_namespaced_deployment_scale(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Scaled deployment '{target_name}' to {val}"
                print(f"   └─ ✅ Deployment scaled to {val}.")

            elif action_type == "increase_memory_limit":
                # Patches the first container in the Deployment to increase memory limit
                deployment = apps_v1_api.read_namespaced_deployment(name=target_name, namespace=namespace)
                actual_container_name = deployment.spec.template.spec.containers[0].name
                
                # 2. Apply the patch using the correct container name
                body = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": actual_container_name, 
                                        "resources": {"limits": {"memory": val}}
                                    }
                                ]
                            }
                        }
                    }
                }
                apps_v1_api.patch_namespaced_deployment(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Increased memory limit for '{target_name}' (container: {actual_container_name}) to {val}"
                print(f"   └─ ✅ Memory limit increased on container '{actual_container_name}'.")

            elif action_type == "increase_cpu_limit":
                body = {"spec": {"template": {"spec": {"containers": [{"name": target_name, "resources": {"limits": {"cpu": val}}}]}}}}
                apps_v1_api.patch_namespaced_deployment(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Increased CPU limit for '{target_name}' to {val}"
                print(f"   └─ ✅ CPU limit increased.")

            # ---------------------------------------------------------
            # 2. IN-PROCESS APPLICATION OPERATIONS (Via k8s stream exec)
            # ---------------------------------------------------------
            elif action_type == "kill_idle_db_connections":
                # Target must be the Postgres pod name
                exec_cmd = ["psql", "-U", "postgres", "-c", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle';"]
                resp = stream(v1_api.connect_get_namespaced_pod_exec, name=target_name, namespace=namespace, command=exec_cmd, stderr=True, stdin=False, stdout=True, tty=False)
                output_logs += f"\n[SUCCESS] Dropped idle DB connections on '{target_name}': {resp}"
                print(f"   └─ ✅ DB Connections dropped.")

            elif action_type == "kill_container_process":
                pid = val or "1"
                exec_cmd = ["/bin/sh", "-c", f"kill -15 {pid} || kill -9 {pid}"]
                resp = stream(v1_api.connect_get_namespaced_pod_exec, name=target_name, namespace=namespace, command=exec_cmd, stderr=True, stdin=False, stdout=True, tty=False)
                output_logs += f"\n[SUCCESS] Killed PID {pid} in '{target_name}': {resp}"
                print(f"   └─ ✅ Process terminated.")

            elif action_type == "abort_hanging_requests":
                threshold = val or "30"
                exec_cmd = ["curl", "-s", "-X", "POST", f"http://127.0.0.1:8080/admin/requests/abort?threshold={threshold}"]
                resp = stream(v1_api.connect_get_namespaced_pod_exec, name=target_name, namespace=namespace, command=exec_cmd, stderr=True, stdin=False, stdout=True, tty=False)
                output_logs += f"\n[SUCCESS] Aborted requests exceeding {threshold}s on '{target_name}': {resp}"
                print(f"   └─ ✅ Hanging requests aborted.")

            elif action_type == "trip_circuit_breaker":
                service = val or "all"
                exec_cmd = ["curl", "-s", "-X", "POST", f"http://127.0.0.1:8080/admin/circuit-breaker/trip?service={service}"]
                resp = stream(v1_api.connect_get_namespaced_pod_exec, name=target_name, namespace=namespace, command=exec_cmd, stderr=True, stdin=False, stdout=True, tty=False)
                output_logs += f"\n[SUCCESS] Tripped circuit breaker for '{service}' on '{target_name}': {resp}"
                print(f"   └─ ✅ Circuit breaker tripped.")

            elif action_type == "clear_app_cache":
                exec_cmd = ["curl", "-s", "-X", "POST", "http://127.0.0.1:8080/admin/cache/clear"]
                resp = stream(v1_api.connect_get_namespaced_pod_exec, name=target_name, namespace=namespace, command=exec_cmd, stderr=True, stdin=False, stdout=True, tty=False)
                output_logs += f"\n[SUCCESS] Cleared application cache on '{target_name}': {resp}"
                print(f"   └─ ✅ Cache cleared.")

            else:
                output_logs += f"\n[WARNING] Action type '{action_type}' is not currently implemented in SDK router."

        except Exception as e:
            error_msg = f"[ERROR] Kubernetes API execution failed for {target_name}: {str(e)}"
            output_logs += f"\n{error_msg}"
            print(f"   └─ ❌ K8s SDK Error: {e}")
            break
    
    return {"execution_logs": output_logs, "hitl_route": "execute"}



def reporter_node(state: AgentState):
    """Generates a concise, actionable SRE Post-Mortem or Human Handoff report."""
    print("\n📝 [REPORTER] Generating concise SRE Post-Mortem...")
    import datetime
    import json
    from langchain_core.messages import SystemMessage, HumanMessage
    
    alert = state.get("alert_payload", {})
    context = state.get("context_gathered", "")
    plan = state.get("proposed_plan")
    logs = state.get("execution_logs", "")
    hitl_route = state.get("hitl_route", "execute")
    critic_feedback = state.get("critic_feedback", "No human feedback provided.")
    
    fallback_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start_time_str = state.get("incident_start_time", fallback_start)
    end_time_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    try:
        t_start = datetime.datetime.fromisoformat(start_time_str)
        t_end = datetime.datetime.fromisoformat(end_time_str)
        mttr_seconds = int((t_end - t_start).total_seconds())
    except Exception:
        mttr_seconds = 0

    alert_name = alert.get("alertname") or alert.get("labels", {}).get("alertname", "UnknownAlert")
    target = alert.get("labels", {}).get("deployment") or alert.get("labels", {}).get("pod", "UnknownResource")

    prompt = f"""
    You are an elite Site Reliability Engineer writing a highly concise, actionable incident hand-off report.
    Do NOT write verbose fluff. Use bullet points and exact technical details. 
    
    ### Incident Data
    - Alert: {alert_name}
    - Target: {target}
    - Resolution State: {hitl_route.upper()} (execute = applied fix, abort = human stopped it, replan = human altered it)
    - MTTR: {mttr_seconds} seconds
    
    ### What the AI Found (Context & Logs)
    {context}
    
    ### What the AI Proposed
    {plan}
    
    ### Human Feedback / Critic Overrides
    {critic_feedback}
    
    ### Execution Results
    {logs}
    
    Format the report in Markdown using exactly these sections. Keep it brief and scannable:

    ## 🚨 Incident Summary
    (1-2 sentences max on what broke and the final state)
    
    ## 🤖 AI Diagnosis & Proposed Actions
    (Brief bullet points on what the agent found and what it wanted to do)
    
    ## 👤 Human Intervention
    (Detail exactly what the human operator did. Did they abort? Did they provide feedback to change the plan? If they just clicked approve, state "Approved without modification".)
    
    ## 🔧 Execution Status
    (Did the fix run successfully? If aborted, state NO EXECUTION.)
    
    ## 🔎 Actionable Handoff (For Human SREs)
    (CRITICAL: If the issue was aborted, unresolved, or novel, extract the exact error lines, failing PIDs, or stack traces from the context here so the on-call engineer can fix it offline. If the issue is completely resolved, state "No further action required.")
    """

    report_response = llm.invoke([
        SystemMessage(content="You are an expert SRE writer creating concise, strictly formatted incident handoffs."),
        HumanMessage(content=prompt)
    ])
    
    post_mortem_markdown = report_response.content

    # Call the tool to commit to your repo
    commit_url = commit_post_mortem_to_repo(
        post_mortem_content=post_mortem_markdown,
        alert_name=alert_name,
        target_resource=target
    )

    return {
        "incident_end_time": end_time_str,
        "post_mortem_md": post_mortem_markdown,
        "github_commit_url": commit_url
    }
    
    
    
    
    
workflow = StateGraph(AgentState)

# Add all nodes
workflow.add_node("investigator_node", investigator_node)
workflow.add_node("tools", tool_node)
workflow.add_node("proposer_node", proposer_node)
workflow.add_node("critic_node", critic_node)
workflow.add_node("executor_node", executor_node)
workflow.add_node("reporter_node", reporter_node)

# Set entry point
workflow.set_entry_point("investigator_node")

# 1. Investigator Routing (goes to tools if tools are called, else moves to proposer)
workflow.add_conditional_edges("investigator_node", tools_condition, {"tools": "tools", END: "proposer_node"})
workflow.add_edge("tools", "investigator_node")

# 2. Proposer directly to Critic


workflow.add_edge("proposer_node", "critic_node")

workflow.add_edge("critic_node", "executor_node")


def hitl_router(state: AgentState):
    route = state.get("hitl_route", "execute")
    if route == "replan":
        return "proposer_node"  # Loop back to generate a new plan
    return "reporter_node"      # Continue forward for 'execute' or 'abort'



# Replace the old executor -> reporter edge with this conditional edge
workflow.add_conditional_edges(
    "executor_node", 
    hitl_router, 
    {
        "proposer_node": "proposer_node",
        "reporter_node": "reporter_node"
    }
)


workflow.add_edge("reporter_node", END)



# IMPORTANT: You MUST use a checkpointer so the graph can pause and resume for HITL
memory = MemorySaver()
agent_app = workflow.compile(checkpointer=memory)

