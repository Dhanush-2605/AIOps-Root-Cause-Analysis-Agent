from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from agent_state import AgentState
from agent_tools import fetch_filtered_pod_logs, get_pod_status
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.graph import StateGraph, END
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
def investigator_node(state: AgentState):
    """Analyzes the alert payload and uses tools to gather logs/metrics."""
    # Ensure messages is a list of BaseMessage objects
    messages = list(state.get("messages", []))
    
    # If starting fresh, fetch the dict and wrap it inside a HumanMessage
    if not messages:
        alert_data = state.get("alert_payload", {})
        
        # Convert dictionary to formatted JSON string so the LLM can read it cleanly
        alert_text = (
            "Prometheus Alert Triggered:\n"
            f"```json\n{json.dumps(alert_data, indent=2)}\n```\n"
            "Please investigate this alert using your available tools."
        )
        
        messages.append(HumanMessage(content=alert_text))
    
    # Define system instructions
    sys_msg = SystemMessage(
        content=f"You are an Elite Kubernetes SRE.\n{SOP_CONTEXT}\nGather all necessary logs and metrics to diagnose the issue."
    )
    
    # Send [SystemMessage, HumanMessage, ...] to Gemini
    response = llm_with_tools.invoke([sys_msg] + messages)
    
    # Update state: return the input messages + model's response (AIMessage)
    return {"messages": messages + [response]}


workflow = StateGraph(AgentState)

workflow.add_node("investigator_node", investigator_node)
workflow.add_node("tools", tool_node)

workflow.set_entry_point("investigator_node")

# Route automatically: if LLM calls a tool -> "tools" node, else -> END
workflow.add_conditional_edges(
    "investigator_node",
    tools_condition,
    {
        "tools": "tools",
        END: END
    }
)

# Loop back from tools to the investigator to interpret results
workflow.add_edge("tools", "investigator_node")

agent_app = workflow.compile()

# 4. Run the Test
if __name__ == "__main__":
    test_state = {
        "messages": [],
        "alert_payload": sample_alert_payload
    }

    print("--- STARTING AGENT INVESTIGATION ---")
    result = agent_app.invoke(test_state)
    print(result)
    
    # Print out conversation history
    # for msg in result["messages"]:
    #     print(f"\n[{msg.__class__.__name__}]:")
    #     print(msg.content or msg.tool_calls)