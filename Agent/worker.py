import redis
import json
from orchestator import agent_app  # Your LangGraph agent

r = redis.Redis(host='localhost', port=6379, db=0)

print("Agent Worker started. Waiting for alerts...")

while True:
    # BLPOP blocks until an alert is available in the queue
    _, raw_alert = r.blpop("alert_queue")
    alert_data = json.loads(raw_alert)
    
    target = alert_data["labels"].get("deployment") or alert_data["labels"].get("pod")
    namespace = alert_data["labels"].get("namespace", "default")
    lock_key = f"active_agent:{namespace}:{target}"
    
    # Set lock with TTL (e.g., 10 minutes max run time)
    r.set(lock_key, "running", ex=600)
    
    try:
        print(f"\nProcessing queued alert for: {target}")
        
        # Build state and run your LangGraph agent
        initial_state = {
            "messages": [],
            "alert_payload": alert_data,
            "verification_attempts": 0,
            "is_safe": True,
            "critic_feedback": ""
        }
        
        # Invoke your existing agent execution loop
        agent_app.invoke(initial_state, config={"configurable": {"thread_id": target}})
        
    finally:
        # Release lock when done so future alerts for this resource can be processed
        r.delete(lock_key)