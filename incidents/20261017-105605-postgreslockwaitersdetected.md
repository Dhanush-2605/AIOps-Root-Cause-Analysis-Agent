# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T10:55:57.891872+00:00 |
| Report generated (UTC) | 2026-10-17T10:56:01.425391+00:00 |
| Duration (alert to report) | 3s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary
The **PostgresLockWaitersDetected** alert was triggered because several queries were blocked on row locks in the `employee_data` table. Investigation identified a long‑running `SELECT … FOR UPDATE` transaction (PID 1899) that held the lock for **8 minutes 14 seconds**, causing other transactions (PIDs 1939, 1940, 1941) to wait. No automated remediation was performed; the incident remains pending human intervention.

## Root Cause
- A set of long‑running `SELECT … FOR UPDATE` transactions held row locks on `employee_data`. The blocking transaction (PID 1899) held the lock for **8 minutes 14 seconds**, directly causing the alert. **Confidence:** high (based on direct lock‑wait observations).  

## Supporting Evidence
- `blocked_pid` 1939 blocked by `blocking_pid` 1940 for **0:07:10.213216** while both executed `SELECT * FROM employee_data WHERE id = $1 FOR UPDATE`.
- `blocked_pid` 1940 blocked by `blocking_pid` 1899 for **0:08:14.028299** while both executed `SELECT * FROM employee_data WHERE id = $1 FOR UPDATE`.
- `blocked_pid` 1941 blocked by `blocking_pid` 1940 for **0:07:10.213216** while both executed `SELECT * FROM employee_data WHERE id = $1 FOR UPDATE`.
- `blocked_pid` 1941 blocked by `blocking_pid` 1939 for **0:07:10.117591** while both executed `SELECT * FROM employee_data WHERE id = $1 FOR UPDATE`.
- Root‑cause statement: “A set of long‑running `SELECT … FOR UPDATE` transactions are holding row locks on `employee_data`. The blocking transaction (PID 1899) has held the lock for **8 minutes 14 seconds**, and other transactions (PIDs 1939, 1940, 1941) are blocked waiting on the same lock.” **Confidence:** high.
- Evidence gaps: none.

## Proposed Remediation
- **Terminate the blocking transaction** (PID 1899) to release the row lock.  
  - *Target:* PostgreSQL instance hosting `employee_data`.  
  - *Reasoning:* The blocking transaction is the sole source of lock contention; terminating it will unblock the waiting queries.  
- **Identify the PostgreSQL pod name** (target_name) required for the `kill_idle_transaction` action.  
  - *Target:* Kubernetes cluster where the PostgreSQL pod runs.  
  - *Reasoning:* The remediation plan could not be automated because the pod identifier was not available in the investigation data.  

No other automated actions were proposed; human approval is required.

## Operator Review
> **Operator feedback:** None  

## Execution Result
No actions were executed.

## Follow‑up Actions
1. **Locate the PostgreSQL pod** that hosts the `employee_data` database.  
   - Command (example): `kubectl get pods -n <namespace> -l app=postgresql`  
2. **Terminate the blocking transaction** once the pod is identified.  
   - Connect to the database: `kubectl exec -it <postgres-pod> -- psql -U <user> -d <database>`  
   - Run: `SELECT pg_terminate_backend(1899);`  
3. **Verify lock release** by re‑running the lock‑wait query or checking `pg_locks`.  
   - Example: `SELECT * FROM pg_locks WHERE pid = 1899;` (should return no rows).  
4. **Monitor for recurrence** of `PostgresLockWaitersDetected` alerts for at least 30 minutes after termination.  
5. **Implement preventive measures** such as query timeout limits or application‑level lock handling to avoid long‑running `SELECT … FOR UPDATE` transactions.  
6. **Document the incident** in the runbook, including the PID values (1899, 1939, 1940, 1941) and the lock duration observed.  