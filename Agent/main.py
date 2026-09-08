from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, change this to ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    
triggered_threads = []

@app.post("/alert")
async def receive_alert(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    alerts = payload.get("alerts", [])
    
    
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

# In backend/main.py

@app.get("/incidents")
async def get_incidents():
    """React polls this endpoint to populate the left pane and right pane."""
    frontend_incidents = []
    
    for tid in triggered_threads:
        config = {"configurable": {"thread_id": tid}}
        try:
            state_snapshot = agent_app.get_state(config)
            
            if not state_snapshot or not state_snapshot.values:
                continue
                
            state_vals = state_snapshot.values
            alert = state_vals.get("alert_payload", {})
            alert_name = alert.get("labels", {}).get("alertname", "Unknown Alert")
            target = alert.get("labels", {}).get("deployment", alert.get("labels", {}).get("pod", "Unknown Resource"))
            
            status = "executing"
            if state_snapshot.tasks and state_snapshot.tasks[0].interrupts:
                status = "paused"
            elif not state_snapshot.next:
                status = "completed"

            # 1. Extract the Context (What the agent found in Prometheus/Logs)
            context_gathered = state_vals.get("context_gathered", "No context gathered.")

            # 2. Extract the detailed plan and reasoning safely
            raw_plan = state_vals.get("proposed_plan")
            detailed_plan = []
            
            if raw_plan:
                # Handle both dict and Pydantic object serialization
                raw_actions = getattr(raw_plan, "proposed_actions", []) if not isinstance(raw_plan, dict) else raw_plan.get("proposed_actions", [])
                
                for act in raw_actions:
                    act_type = getattr(act, "action_type", "") if not isinstance(act, dict) else act.get("action_type", "")
                    tgt = getattr(act, "target_resource", getattr(act, "target_name", "")) if not isinstance(act, dict) else act.get("target_resource", act.get("target_name", ""))
                    reason = getattr(act, "reasoning", "No reasoning provided.") if not isinstance(act, dict) else act.get("reasoning", "No reasoning provided.")
                    
                    detailed_plan.append({"action": act_type, "target": tgt, "reasoning": reason})

            logs = [{"timestamp": "Sys", "node_name": "Alert", "message": f"Received: {alert_name}"}]
            if state_vals.get("execution_logs"):
                logs.append({"timestamp": "Sys", "node_name": "Executor", "message": state_vals.get("execution_logs")})

            frontend_incidents.append({
                "thread_id": tid,
                "alert_name": alert_name,
                "target_resource": target,
                "status": status,
                "context_gathered": context_gathered,  
                "detailed_plan": detailed_plan,        
                "logs": logs
            })
        except Exception as e:
            print(f"Error fetching state for {tid}: {e}")
            
    return {"active_threads": frontend_incidents}


#  THE HUMAN-IN-THE-LOOP (HITL) APPROVAL ENDPOINT
class ApprovalRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "abort", "feedback"] # Forces one of these three strings
    feedback: Optional[str] = None # The

@app.post("/approve")
async def approve_action(approval: ApprovalRequest):
    """Resume a paused agent thread with a decision and optional feedback."""
    print("CLICKED APPROVE/ABORT BUTTON")
    config = {"configurable": {"thread_id": approval.thread_id}}
    
    user_response_dict = {
        "decision": approval.decision,
        "feedback": approval.feedback
    }
    
    print(f"\n▶️ [RESUMING AGENT] Thread {approval.thread_id} | Decision: {approval.decision}")
    
    try:
        # Resume the paused state
        print(f"[DEBUG] Invoking agent_app with user_response_dict: {user_response_dict}")
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