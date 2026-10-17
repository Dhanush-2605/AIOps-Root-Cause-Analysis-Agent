# Incident Report: HangingRequestsDetected

| Field | Value |
|---|---|
| Alert | HangingRequestsDetected |
| Target | backend-deployment-5d9b4598dc-4bg5m |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T10:03:57.897087+00:00 |
| Report generated (UTC) | 2026-10-17T10:05:31.063824+00:00 |
| Duration (alert to report) | 1m 33s |
| Outcome | Remediation executed |

---

## Summary  
The **HangingRequestsDetected** alert fired for the pod `backend-deployment-5d9b4598dc-4bg5m`. Investigation identified three POST requests to `/api/payments/process` that had been open for approximately 78 seconds, exceeding the 10‑second hang threshold. An abort action was attempted but the execution did not confirm success, leaving the hanging requests unresolved.

## Root Cause  
- The `/api/payments/process` endpoint is experiencing hanging requests, as shown by the three in‑flight requests reported by `fetch_hanging_requests` (ageSec ≈ 78).  
- Confidence: **high** (supported by log warnings and absence of database lock contention).  

## Supporting Evidence  
- `fetch_hanging_requests` returned a count of **3** hanging requests, each with `method: "POST"`, `endpoint: "/api/payments/process"` and `ageSec: 78`.  
- `fetch_filtered_pod_logs` contains multiple warning lines such as:  
  ```json
  {"level":"warn","timestamp":"2026-10-17T10:02:51.029Z","message":"Request exceeded hang threshold - possible timeout","endpoint":"/api/payments/process","method":"POST","request_id":"1792231360611-aju4h6x","age_sec":10,"threshold_sec":10}
  ```  
  and similar entries for request IDs `1792231360612-prak…` and `1792231360614-5wjfaqi`.  
- `get_blocking_transactions` reported **no blocking transactions**, indicating the hang is not due to database lock contention.  

## Proposed Remediation  
- **Action:** `abort_hanging_requests`  
  - **Target:** `backend-deployment-5d9b4598dc-4bg5m` (namespace `default`)  
  - **Value:** `10` (seconds)  
  - **Reasoning:** The three POST requests to `/api/payments/process` have been open for ~78 seconds, exceeding the configured 10‑second hang threshold. Aborting requests older than the threshold will free the stuck processing threads and prevent further resource exhaustion.  

## Operator Review  
Approved without modification. *(Operator feedback: None)*  

## Execution Result  
- **Abort hanging requests** on `backend-deployment-5d9b4598dc-4bg5m`:  
  - Result: Execution failed. Log entry:  
    ```
    [ERROR] Execution failed for backend-deployment-5d9b4598dc-4bg5m: Abort call to 'backend-deployment-5d9b4598dc-4bg5m' did not confirm success (got: '{'status': 'success', 'abortedRequests': 3, 'endpoints': ['/api/payments/process', '/api/payments/process', '/api/payments/process']}')
    ```  

## Follow-up Actions  
1. **Re‑attempt abort**:  
   - Run the abort command again, verifying the response payload for a confirmed success status.  
   - Example (adjust as needed for the automation tool):  
     ```bash
     kubectl exec -n default backend-deployment-5d9b4598dc-4bg5m -- curl -X POST http://localhost:8080/abort?threshold=10
     ```  
2. **If abort continues to fail**, manually terminate the affected requests:  
   - Identify the process handling the requests (e.g., via `ps aux | grep <service>`).  
   - Send a `SIGTERM` to the specific worker threads or restart the pod:  
     ```bash
     kubectl delete pod backend-deployment-5d9b4598dc-4bg5m -n default
     ```  
3. **Collect additional logs** after each attempt to confirm whether the hanging requests are cleared:  
   - Run `kubectl logs backend-deployment-5d9b4598dc-4bg5m -n default --since=5m` and search for `"Request exceeded hang threshold"` or `"Requests aborted"` messages.  
4. **Post‑mortem code review** of the `/api/payments/process` handler to identify the application‑level block causing the hang.  
5. **Update alerting** to include a secondary notification if abort actions fail repeatedly, to reduce mean‑time‑to‑recovery.  

*If the abort succeeds in a subsequent attempt, close the incident and add a preventive action to implement proper timeout handling in the endpoint code.*