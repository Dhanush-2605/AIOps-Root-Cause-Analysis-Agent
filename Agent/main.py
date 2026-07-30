from fastapi import FastAPI, Request
import uvicorn
import json

app = FastAPI()

@app.post("/webhook")
async def receive_alert(request: Request):
    # 1. Parse the incoming JSON payload from Alertmanager
    payload = await request.json()
    
    # 2. Print a highly visible header to the console
    print("\n" + "="*50)
    print("🚨 NEW ALERT RECEIVED FROM ALERTMANAGER 🚨")
    print("="*50)
    
    # 3. Pretty-print the JSON so you can inspect the structure
    print(json.dumps(payload, indent=2))
    
    print("="*50 + "\n")
    
    # 4. Acknowledge receipt (Alertmanager will retry if it doesn't get a 200 OK)
    return {"status": "success", "message": "Alert acknowledged by AI Agent"}

if __name__ == "__main__":
    # Runs the server on port 8000 to match our Alertmanager YAML config
    uvicorn.run(app, host="0.0.0.0", port=8000)