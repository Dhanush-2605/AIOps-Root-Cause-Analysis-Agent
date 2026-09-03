from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from agent_state import AgentState, RemediationPlan, CriticReview,K8sAction
from agent_tools import fetch_filtered_pod_logs, get_pod_status
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.graph import StateGraph, END

from agent_tools import get_gke_client

import datetime
from langgraph.types import interrupt
from langgraph.checkpoint.memory import MemorySaver

from github_utils import commit_post_mortem_to_repo
import json
from model import llm



tools = [fetch_filtered_pod_logs, get_pod_status]
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

sample_alert_payload = {
    "alert_name": "KubePodCrashLooping",
    "severity": "critical",
    "namespace": "default",
    "pod_name": "logger-error",
    "container": "logger-error",
    "summary": "Pod logger-error is experiencing high error rates",
    "description": "Pod default/logger-error has generated multiple connection error logs in the last 5 minutes.",
    "started_at": "2026-08-25T14:30:00Z"
}

SOP_CONTEXT = """

SYSTEM ARCHITECTURE & SOPs:
- The system is a Kubernetes cluster running multiple microservices.
-Right now these 3 services are running they are NOT connected to each therlogger-error ,logger-info ,nginx-app


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
    """Generates or adjusts a remediation plan based on context and critic feedback."""
    context = state.get("context_gathered", "")
    alert = state.get("alert_payload", {})
    feedback = state.get("critic_feedback", "")
    is_safe = state.get("is_safe", True)
    
    structured_llm = llm.with_structured_output(RemediationPlan)
    
    sys_msg = SystemMessage(
        content=(
            "You are an SRE Remediation Expert.\n"
            "Propose a structured JSON RemediationPlan containing valid K8sAction objects.\n"
            "Valid action_types: 'delete_pod', 'scale_deployment', 'scale_statefulset', "
            "'restart_deployment', 'increase_memory_limit', 'increase_cpu_limit', 'kill_idle_db_connections'."
        )
    )
    
    # If the Critic rejected the last attempt, explicitly tell the model what to fix
    feedback_prompt = ""
    if not is_safe and feedback:
        feedback_prompt = f"\n\n⚠️ PREVIOUS PLAN REJECTED BY CRITIC:\n{feedback}\nFix the actions to address this feedback."

    prompt = (
        f"Alert:\n{json.dumps(alert, indent=2)}\n\n"
        f"Investigation Context:\n{context}"
        f"{feedback_prompt}\n\n"
        "Propose a safe remediation plan."
    )
    
    plan: RemediationPlan = structured_llm.invoke([sys_msg, HumanMessage(content=prompt)])
    action_list = [f"{a.action_type}({a.target_name})" for a in plan.proposed_actions]
    
    print(f"   └─ Proposed Plan: {action_list}")
    return {"proposed_plan": plan}


CRITIC_SYS_MSG = SystemMessage(
    content="You are a strict Kubernetes Production Gatekeeper and Safety Critic. Review proposed commands against cluster SOPs."
)

def critic_node(state: AgentState):
    """Reviews the proposed commands for blast radius and safety."""
    plan = state["proposed_plan"]
    feedback_history = state.get("critic_feedback", "")
    
    # 1. BIND THE PYDANTIC MODEL (This converts the AIMessage to CriticReview)
    structured_llm = llm.with_structured_output(CriticReview)
    
    # Format the Kubernetes actions nicely for the prompt
    actions_summary = "\n".join([
        f"- Action: {act.action_type} | Target: {act.target_name} | Namespace: {act.namespace} | Value: {act.value}"
        for act in plan.proposed_actions
    ])
    
    prompt = f"""
    Review this proposed fix for safety and architectural correctness:
    Root Cause: {plan.root_cause}
    
    Proposed Actions: 
    {actions_summary}
    
    SOPs:
    {SOP_CONTEXT}
    
    Previous Critic Feedback (if any): {feedback_history}
    
    Will these actions cause data loss, downtime, or violate SOPs?
    """
    
    # 2. INVOKE THE STRUCTURED LLM
    # Because we used with_structured_output, 'review' is now a CriticReview Pydantic object
    review: CriticReview = structured_llm.invoke([CRITIC_SYS_MSG, HumanMessage(content=prompt)])
    is_safe_bool = bool(review.is_safe)
    attempts = state.get("verification_attempts", 0) + 1
    
# Log the Critic's decision
    if is_safe_bool:
        print(f"   └─ ✅ RESULT: APPROVED")
    else:
        print(f"   └─ ❌ RESULT: REJECTED")
        print(f"   └─ REASON: {review.feedback}")
        
        
    # 3. SAFELY ACCESS THE PYDANTIC FIELDS
    return {
        "critic_feedback": review.feedback,
        "is_safe": is_safe_bool,
        "verification_attempts": attempts
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
    # 
    v1_api, apps_v1_api = get_gke_client()


    plan = state.get("proposed_plan")
    is_safe = state.get("is_safe", True)
    critic_feedback = state.get("critic_feedback", "")
    
    if not plan:
        return {"execution_logs": "No remediation plan found."}

    # Extract actions safely whether 'plan' is a Dict or Pydantic Model
    raw_actions = get_action_attr(plan, "proposed_actions", [])
    if not raw_actions:
        return {"execution_logs": "No actions to execute in proposed plan."}

    output_logs = ""
    
    for action in raw_actions:
        action_type = get_action_attr(action, "action_type")
        target_name = get_action_attr(action, "target_name")
        namespace = get_action_attr(action, "namespace", "default")
        val = get_action_attr(action, "value")

        action_summary = f"{action_type} on {target_name} (namespace: {namespace})"
        if val:
            action_summary += f" with value: {val}"

        warning_banner = ""
        if not is_safe:
            warning_banner = f"\n⚠️ CRITIC SAFETY WARNING: {critic_feedback}\n"

        # Trigger Human-in-the-Loop Pause
        user_response = interrupt({
            "is_safe": is_safe,
            "warning": warning_banner,
            "question": f"{warning_banner}Action to execute: {action_summary}\nApprove action? (y/n): "
        })

        # Process User Input after resume
        if str(user_response).strip().lower() not in ["y", "yes"]:
            output_logs += f"\n[ABORTED BY USER] Execution canceled for: {action_summary}"
            print(f"\n❌ Execution aborted by user for target: {target_name}")
            break

        print(f"\n🚀 [EXECUTING K8S SDK] Calling API for: {action_summary}...")

        try:
            if action_type == "delete_pod":
                res = v1_api.delete_namespaced_pod(name=target_name, namespace=namespace)
                output_logs += f"\n[SUCCESS] Deleted pod '{target_name}' in namespace '{namespace}'"
                print(f"   └─ ✅ Pod deleted successfully.")

            elif action_type == "restart_deployment":
                import datetime
                now = datetime.datetime.utcnow().isoformat() + "Z"
                body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
                apps_v1_api.patch_namespaced_deployment(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Restarted deployment '{target_name}' in namespace '{namespace}'"
                print(f"   └─ ✅ Deployment restart triggered.")

            elif action_type == "scale_deployment":
                body = {"spec": {"replicas": int(val)}}
                apps_v1_api.patch_namespaced_deployment_scale(name=target_name, namespace=namespace, body=body)
                output_logs += f"\n[SUCCESS] Scaled deployment '{target_name}' to {val}"
                print(f"   └─ ✅ Deployment scaled to {val}.")

            else:
                output_logs += f"\n[WARNING] Action type '{action_type}' is not currently implemented in SDK router."

        except Exception as e:
            error_msg = f"[ERROR] Kubernetes API execution failed for {target_name}: {str(e)}"
            output_logs += f"\n{error_msg}"
            print(f"   └─ ❌ K8s SDK Error: {e}")
            break

    return {"execution_logs": output_logs}

def reporter_node(state: AgentState):
    """Generates an SRE Blameless Post-Mortem in Markdown and commits it to GitHub."""
    print("\n📝 [REPORTER] Generating automated SRE Post-Mortem...")
    
    alert = state.get("alert_payload", {})
    context = state.get("context_gathered", "")
    plan = state.get("proposed_plan")
    logs = state.get("execution_logs", "")
    start_time_str = state.get("incident_start_time", datetime.datetime.utcnow().isoformat())
    end_time_str = datetime.datetime.utcnow().isoformat()
    
    try:
        t_start = datetime.datetime.fromisoformat(start_time_str)
        t_end = datetime.datetime.fromisoformat(end_time_str)
        mttr_seconds = int((t_end - t_start).total_seconds())
    except Exception:
        mttr_seconds = 0

    alert_name = alert.get("alertname") or alert.get("labels", {}).get("alertname", "UnknownAlert")
    target = alert.get("labels", {}).get("deployment") or alert.get("labels", {}).get("pod", "UnknownResource")

    prompt = f"""
    You are a Principal Site Reliability Engineer (SRE). Generate a comprehensive, professional, blameless incident post-mortem in Markdown format based on the following execution data:

    ### Incident Overview
    - Alert Name: {alert_name}
    - Target Resource: {target}
    - Incident Start: {start_time_str}
    - Resolution Completed: {end_time_str}
    - Calculated MTTR: {mttr_seconds} seconds

    ### Investigation Findings & Context
    {context}

    ### Executed Remediation Plan
    {plan}

    ### K8s Execution Logs
    {logs}

    Please write the post-mortem using this structure:
    # Incident Post-Mortem: {alert_name} ({target})
    ## Executive Summary
    ## Incident Timeline (MTTD / MTTR)
    ## Root Cause Analysis (5 Whys)
    ## Remediation Actions Executed
    ## Recommended Long-Term Preventive Actions
    """

    report_response = llm.invoke([
        SystemMessage(content="You are an expert SRE writer creating blameless post-mortems."),
        HumanMessage(content=prompt)
    ])
    
    post_mortem_markdown = report_response.content

    # Push directly to the repo
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

# Set entry point
workflow.set_entry_point("investigator_node")

# 1. Investigator Routing (goes to tools if tools are called, else moves to proposer)
workflow.add_conditional_edges("investigator_node", tools_condition, {"tools": "tools", END: "proposer_node"})
workflow.add_edge("tools", "investigator_node")

# 2. Proposer directly to Critic


workflow.add_edge("proposer_node", "critic_node")

workflow.add_edge("critic_node", "executor_node")

# 4. Executor finishes the workflow
workflow.add_edge("executor_node", END)

# IMPORTANT: You MUST use a checkpointer so the graph can pause and resume for HITL
memory = MemorySaver()
agent_app = workflow.compile(checkpointer=memory)
# 4. Run the Test
from langgraph.types import Command
import uuid
if __name__ == "__main__":


    thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    
    test_state = {
        "messages": [],
        "alert_payload": sample_alert_payload,
        "verification_attempts": 0,
        "is_safe": True,
        "critic_feedback": ""
    }

    print("--- STARTING AGENT INVESTIGATION & REMEDIATION ---")
    
    # Initial run with thread_config passed in
    agent_app.invoke(test_state, config=thread_config)
    
    # HITL Loop
    while True:
        current_state = agent_app.get_state(thread_config)
        
        # Check if paused at an interrupt()
        if current_state.next and current_state.tasks and current_state.tasks[0].interrupts:
            interrupt_data = current_state.tasks[0].interrupts[0].value
            
            print(f"\n🚨 [HITL PAUSE] {interrupt_data.get('question')}")
            user_input = input("Approve action? (y/n): ")
            
            # Resume graph with user decision
            agent_app.invoke(Command(resume=user_input), config=thread_config)
        else:
            break

    # Print final status
    final_state = agent_app.get_state(thread_config).values
    print("\n================ WORKFLOW COMPLETED ================")
    print(f"Execution Logs: {final_state.get('execution_logs', 'No actions executed.')}")