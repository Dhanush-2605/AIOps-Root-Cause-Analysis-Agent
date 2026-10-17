# Incident Report: HangingRequestsDetected

| Field | Value |
|---|---|
| Alert | HangingRequestsDetected |
| Target | backend-deployment-5d9b4598dc-4bg5m |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T08:21:57.890033+00:00 |
| Report generated (UTC) | 2026-10-17T08:22:54.533963+00:00 |
| Duration (alert to report) | 56s |
| Outcome | Remediation executed |

---

## Summary
The **HangingRequestsDetected** alert fired for `backend-deployment-5d9b4598dc-4bg5m`. Investigation identified three POST requests to `/api/payments/process` that had been open for approximately 91 seconds, with matching warning log entries indicating timeout thresholds were exceeded. An abort‑hanging‑requests action was attempted but failed, leaving the hanging requests unresolved.

## Root Cause
- The `/api/payments/process` endpoint was handling three POST requests that remained in‑flight for ~91 seconds (**fetch_hanging_requests**).  
- Pod logs contained warning entries `"Request exceeded hang threshold - possible timeout"` for the same request IDs and endpoint (**fetch_filtered_pod_logs**).  
- No database lock contention was detected (**get_blocking_transactions** returned none), indicating the hangs were not caused by blocking transactions.  

**Confidence:** high – the evidence directly shows hanging requests and corresponding timeout warnings, with no alternative contention observed.

## Supporting Evidence
- `fetch_hanging_requests` reported **count: 3** with request IDs `1792225227508-gcmoj4p`, `1792225227511-0itxcex`, `1792225227512-o619b16`, all `POST /api/payments/process`, `ageSec: 91`.  
- `fetch_filtered_pod_logs` showed three warning log lines at `2026-10-17T08:20:40.868Z`:
  - `{"level":"warn","timestamp":"2026-10-17T08:20:40.868Z","message":"Request exceeded hang threshold - possible timeout","endpoint":"/api/payments/process","method":"POST","request_id":"1792225227508-gcmoj4p","age_sec":13,"threshold_sec":10}`
  - (same structure for request IDs `1792225227511-0itxcex` and `1792225227512-o619b16`).  
- `list_pods` confirmed the pod `backend-deployment-5d9b4598dc-4bg5m` was **Running**, ready `1/1`, with **0 restarts**.  
- `get_blocking_transactions` returned **No blocking transactions found**.

## Proposed Remediation
- **Action:** `abort_hanging_requests`  
  - **Target:** `backend-deployment-5d9b4598dc-4bg5m` (namespace `default`)  
  - **Value:** `10` (abort requests older than the 10‑second threshold)  
  - **Reasoning:** Abort requests older than the 10‑second threshold to clear the three hanging POST requests (age ≈ 91 s) observed in `fetch_hanging_requests` and the corresponding timeout warnings in pod logs. This low‑impact action directly addresses the hanging requests without restarting the service.

## Operator Review
Approved without modification.  
> **Critic feedback:** “Approved. The abort_hanging_requests action is directly supported by the evidence of hanging requests and matching log warnings. No higher‑impact actions are needed.”

## Execution Result
- **abort_hanging_requests** on `backend-deployment-5d9b4598dc-4bg5m`: **Failed**  
  - Error: `[ERROR] Execution failed for backend-deployment-5d9b4598dc-4bg5m: Abort call to 'backend-deployment-5d9b4598dc-4bg5m' did not confirm success (got: '')`

## Follow-up Actions
- Verify the abort API endpoint is reachable and returns a success confirmation.  
  ```bash
  curl -X POST http://<service-endpoint>/abort?target=backend-deployment-5d9b4598dc-4bg5m&age=10
  ```  
- Re‑execute the abort action after confirming the API response format.  
- If the abort continues to fail, perform a controlled restart of the affected pod:  
  ```bash
  kubectl rollout restart deployment/backend-deployment
  ```  
- After restart, re‑monitor the `/api/payments/process` endpoint for hanging requests and confirm that no new warnings appear in the pod logs.  
- Document any additional findings (e.g., unexpected error codes, missing confirmations) and update the alert thresholds if false positives are observed.