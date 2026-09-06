from fastapi import FastAPI, Request, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import json
from datetime import datetime, timezone

import uuid

from typing import TypedDict, List, Annotated, Sequence, Literal, Optional


# Import LangGraph execution primitives
from langgraph.types import Command

# Import our compiled agent and state
from orchestator import agent_app

app = FastAPI()

# Helper function to run the agent asynchronously

def run_agent_workflow(initial_state: dict, config: dict):
    """Runs the LangGraph workflow until it hits the HITL interrupt."""
    thread_id = config["configurable"]["thread_id"]
    
    print(f"\n🧠 [AGENT STARTING] Investigating alert in thread: {thread_id}")
    
    # Invoke the graph. It will run until it hits the interrupt() in the executor_node
    result = agent_app.invoke(initial_state, config)
    
    # If the graph was paused by our interrupt(), it surfaces here
    if "__interrupt__" in result:
        interrupt_data = result["__interrupt__"][0].value
        print("\n" + "="*60)
        print("⏸️  AGENT PAUSED: WAITING FOR HUMAN APPROVAL")
        print(f"Thread ID: {thread_id}")
        print(interrupt_data["question"])
        print("-> Send a POST to /approve to execute or abort.")
        print("="*60 + "\n")
    else:
        print(f"\n✅ [AGENT FINISHED] Thread {thread_id} completed without requiring execution.")


# THE INGESTION ENDPOINT (From Alertmanager)

@app.post("/alert")
async def receive_alert(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    alerts = payload.get("alerts", [])
    
    triggered_threads = []
    
    for alert in alerts:
        # Only process active outages, ignore "resolved" messages for the analyzer
        if alert.get("status") == "firing":
            
            # 1. Generate a unique memory thread for this specific incident
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
            
            # 2. Format the starting State for LangGraph
            initial_state = {
                "alert_payload": alert,
                "execution_logs": "",
                "verification_attempts": 0,
        "incident_start_time": datetime.now(timezone.utc).isoformat()
            }
            
            # 3. Push to background so Alertmanager gets a 200 OK immediately
            background_tasks.add_task(run_agent_workflow, initial_state, config)
            
            triggered_threads.append(thread_id)
            
    return {
        "status": "success", 
        "message": "Alerts ingested by AI SRE", 
        "active_threads": triggered_threads
    }


#  THE HUMAN-IN-THE-LOOP (HITL) APPROVAL ENDPOINT
class ApprovalRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "abort", "feedback"] # Forces one of these three strings
    feedback: Optional[str] = None # The

@app.post("/approve")
async def approve_action(approval: ApprovalRequest):
    """Resume a paused agent thread with a decision and optional feedback."""
    config = {"configurable": {"thread_id": approval.thread_id}}
    
    user_response_dict = {
        "decision": approval.decision,
        "feedback": approval.feedback
    }
    
    print(f"\n▶️ [RESUMING AGENT] Thread {approval.thread_id} | Decision: {approval.decision}")
    
    try:
        # Resume the paused state
        result = agent_app.invoke(Command(resume=user_response_dict), config=config)
        
        # Check if the graph looped back and hit the HITL interrupt AGAIN
        if "__interrupt__" in result:
            interrupt_data = result["__interrupt__"][0].value
            print("\n" + "="*60)
            print("⏸️  AGENT PAUSED AGAIN: NEW PLAN REQUIRES APPROVAL")
            print(f"Thread ID: {approval.thread_id}")
            print(interrupt_data["question"])
            print("-> Send a POST to /approve to execute, abort, or give more feedback.")
            print("="*60 + "\n")
            
            return {
                "status": "paused_for_replan",
                "thread_id": approval.thread_id,
                "message": "Agent generated a new plan and is waiting for approval."
            }
            
        # If it didn't pause, it means it executed successfully or aborted
        return {
            "status": "resumed_and_completed",
            "thread_id": approval.thread_id,
            "final_logs": result.get("execution_logs", "Processing...")
        }
        
    except Exception as e:
        return {"error": f"Failed to resume thread. Error: {str(e)}"}
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)