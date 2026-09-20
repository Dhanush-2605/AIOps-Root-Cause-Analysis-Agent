from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

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



MAX_INVESTIGATION_ITERATIONS = 6

INVESTIGATOR_SYS_MSG = SystemMessage(content="""
You are an autonomous SRE Diagnostic Agent. Your ONLY job is investigation and 
root-cause identification — you never execute remediation, even if a tool that 
could do so is available to you.

## Hard rules
1. Never conclude "traffic spike" or "needs more resources" without first checking 
   for resource hoarding: long-running/blocking queries, idle-in-transaction 
   connections, replication lag, or connection leaks. Resource saturation is a 
   symptom, not a cause, until proven otherwise.
2. Every claim you make must be traceable to a specific tool result you gathered. 
   Do not state a cause you cannot cite evidence for.
3. If a tool call fails or times out, note it and continue with remaining tools — 
   do not assume a value for a failed call.
4. When you have enough evidence, stop calling tools and produce your final findings 
   as plain text (no more tool_calls) in this shape:

Error: <alert name + symptom>
Root Cause: <technical explanation, each claim tied to a specific tool result>
Confidence: <high | medium | low | inconclusive>
Evidence Gaps: <what's still unknown, if confidence isn't high>
""")


def investigator_node(state: AgentState):
    messages = list(state.get("messages", []))
    context_gathered = state.get("context_gathered", "")
    iterations = state.get("investigation_iterations", 0)

    # 1. First run
    if not messages:
        alert_data = state.get("alert_payload", {})
        alert_text = (
            "Prometheus Alert Triggered:\n"
            f"```json\n{json.dumps(alert_data, indent=2)}\n```\n"
            "Please investigate this alert using your available tools."
        )
        human_msg = HumanMessage(content=alert_text)
        response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG, human_msg])
        return {
            "messages": [human_msg, response],
            "context_gathered": f"Started investigation for alert: {alert_data.get('alert_name', 'unknown')}",
            "investigation_iterations": 1,
        }

    # 2. Pull any new ToolMessages since the last AI turn and fold them into context_gathered
    new_findings = []
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            new_findings.append(f"- [{m.name}]: {str(m.content)[:800]}")
        elif isinstance(m, AIMessage):
            break
    new_findings.reverse()
    if new_findings:
        context_gathered = context_gathered + "\n" + "\n".join(new_findings)

    iterations += 1

    # 3. Hard cap — force a conclusion instead of trusting the prompt alone
    if iterations >= MAX_INVESTIGATION_ITERATIONS:
        force_msg = HumanMessage(content=(
            f"You have reached the maximum of {MAX_INVESTIGATION_ITERATIONS} tool calls. "
            "Do not call any more tools. Output your final findings now based on the "
            "evidence gathered so far — use confidence: low or inconclusive if the "
            "evidence isn't sufficient."
        ))
        # note: llm without tools bound, so it physically cannot call another tool
        response = llm.invoke([INVESTIGATOR_SYS_MSG] + messages + [force_msg])
        if response.content:
            context_gathered += f"\n[Final Summary - forced by iteration cap]: {response.content}"
        return {
            "messages": [force_msg, response],
            "context_gathered": context_gathered,
            "investigation_iterations": iterations,
        }

    # 4. Normal loop continuation
    response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG] + messages)

    if response.content and not response.tool_calls:
        context_gathered += f"\n[Investigation Finding]: {response.content}"

    return {
        "messages": [response],
        "context_gathered": context_gathered,
        "investigation_iterations": iterations,
    }
    
    
    
    


def proposer_node(state: AgentState):
    context = state.get("context_gathered", "")
    alert = state.get("alert_payload", {})
    feedback = state.get("critic_feedback", "")
    is_safe = state.get("is_safe", True)
    verification_attempts = state.get("verification_attempts", 0)
    execution_logs = state.get("execution_logs", "")

    structured_llm = llm.with_structured_output(RemediationPlan)

    sys_msg = SystemMessage(content="""
You are an SRE Remediation Expert. Propose a structured RemediationPlan.

## Action risk tiers — prefer the lowest tier that addresses the root cause
- Low risk, prefer first: kill_idle_db_connections, abort_hanging_requests, 
  clear_app_cache, trip_circuit_breaker
- Medium risk: restart_deployment, scale_deployment
- High risk, last resort: delete_pod, increase_memory_limit, increase_cpu_limit

Do not reach for a medium/high-risk action if a low-risk one addresses the same 
root cause. Resource-limit increases in particular must NEVER be proposed if the 
investigation found a blocking process, leaked connection, or hoarding query — 
that gets killed, not resourced around.

## Rules
- CRITICAL ANTI-HALLUCINATION RULE: if the investigation context does not reveal a 
  clear root cause, return an empty proposed_actions array with confidence: 
  inconclusive. Do not guess.
- Every action needs a justification citing specific evidence from the investigation 
  context, and an honest blast_radius assessment.
- Set requires_human_review: true for any action with blast_radius 'medium' or 'high', 
  or when confidence is not 'high'.
- ABSOLUTE OVERRIDE RULE: if the feedback contains 'HUMAN OPERATOR OVERRIDE', discard 
  your own analysis and format the human's exact requested action into a K8sAction — 
  but it must still be one of the valid action_types. Do not invent a new action_type 
  even under override.
- If this is a replan after a failed verification, do not propose the same action_type 
  on the same target again — the evidence shows it didn't work; propose something 
  different or return empty with confidence: low if nothing else fits.
""")

    feedback_prompt = ""
    if not is_safe and feedback:
        feedback_prompt = f"\n\n⚠️ PREVIOUS PLAN REJECTED:\n{feedback}\nGenerate a NEW plan strictly addressing this feedback."

    retry_prompt = ""
    if verification_attempts > 0:
        retry_prompt = (
            f"\n\n⚠️ PRIOR ATTEMPT {verification_attempts} DID NOT RESOLVE THE ALERT.\n"
            f"Execution logs from that attempt:\n{execution_logs}\n"
            "Do not repeat the same action — propose an alternative or escalate."
        )

    prompt = (
        f"Alert:\n{json.dumps(alert, indent=2)}\n\n"
        f"Investigation Context:\n{context}"
        f"{feedback_prompt}{retry_prompt}\n\n"
        "Propose a safe remediation plan."
    )

    plan: RemediationPlan = structured_llm.invoke([sys_msg, HumanMessage(content=prompt)])

    if not plan.proposed_actions:
        print(f"   └─ Proposed Plan: [EMPTY - {plan.confidence}]")
    else:
        action_list = [
            f"{a.action_type}({a.target_resource or a.target_name or 'unknown'}, risk={a.blast_radius})"
            for a in plan.proposed_actions
        ]
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
    
    print(f"\n[DEBUG] user_response received from FastAPI: {user_response}")
    
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
            elif action_type == "kill_blocking_transaction":
                pid = int(val)  # fail loud if the LLM produced a non-numeric value, rather than exec'ing garbage SQL
                exec_cmd = ["psql", "-U", "postgres", "-c", f"SELECT pg_terminate_backend({pid});"]
                resp = stream(
                    v1_api.connect_get_namespaced_pod_exec,
                    name=target_name, namespace=namespace, command=exec_cmd,
                    stderr=True, stdin=False, stdout=True, tty=False
                )
                output_logs += f"\n[SUCCESS] Terminated blocking backend PID {pid} on '{target_name}': {resp}"
                print(f"   └─ ✅ Blocking transaction (PID {pid}) terminated.")


            elif action_type == "abort_hanging_requests":
                threshold = val or "30"
                exec_cmd = ["curl", "-s", "-X", "POST", f"http://127.0.0.1:8080/admin/requests/abort?threshold={threshold}"]
                resp = stream(
                    v1_api.connect_get_namespaced_pod_exec,
                    name=target_name, namespace=namespace, command=exec_cmd,
                    stderr=True, stdin=False, stdout=True, tty=False
                )
                output_logs += f"\n[SUCCESS] Aborted requests exceeding {threshold}s on '{target_name}': {resp}"
                print(f"   └─ ✅ Hanging requests aborted.")


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

