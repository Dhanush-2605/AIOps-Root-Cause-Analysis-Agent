from fastapi import FastAPI, BackgroundTasks
import redis
import json

from orchestator import agent_app  
app = FastAPI()
r = redis.Redis(host='localhost', port=6379, db=0)

@app.post("/webhook")
async def handle_alertmanager_webhook(payload: dict):
    # Quick 200 OK response to Alertmanager
    alerts = payload.get("alerts", [])
    
    for alert in alerts:
        target = alert["labels"].get("deployment") or alert["labels"].get("pod")
        namespace = alert["labels"].get("namespace", "default")
        lock_key = f"active_agent:{namespace}:{target}"
        
        # Deduplication check: If agent is already working on this workload, skip/queue quietly
        if r.exists(lock_key):
            print(f"Agent already active for {target}, skipping redundant queueing.")
            continue

        # Push to queue
        r.rpush("alert_queue", json.dumps(alert))
        
    return {"status": "ok", "queued_alerts": len(alerts)}



