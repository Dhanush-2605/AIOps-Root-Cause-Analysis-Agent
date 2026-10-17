# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T12:52:57.888937+00:00 |
| Report generated (UTC) | 2026-10-17T12:53:37.192817+00:00 |
| Duration (alert to report) | 39s |
| Outcome | Remediation executed |

---

## Summary  
The PostgresLockWaitersDetected alert was triggered due to a row‑level lock contention on the `lock_test` table. Investigation identified a blocking transaction (PID 785) holding the lock for approximately 23 seconds, causing another transaction (PID 771) to wait. The blocking transaction was terminated, releasing the lock and clearing the alert condition.

## Root Cause  
- Diagnosis: Row‑level lock contention on table `lock_test` where transaction PID 785 held a lock for ~23 seconds, blocking transaction PID 771. (Supported by the `get_blocking_transactions` finding.)  
- Confidence: **high** (direct evidence of the blocking transaction was obtained; no evidence gaps.)

## Supporting Evidence  
- Alert: `PostgresLockWaitersDetected`.  
- `get_blocking_transactions` output:  
  - Blocked PID: **771** – query `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`  
  - Blocking PID: **785** – same query, lock held for **0:00:23.065469**.  
- Root‑cause statement confirming row‑level lock contention.  
- Execution log: `[SUCCESS] Terminated backend PID 785`.

## Proposed Remediation  
- **Action:** `kill_idle_transaction`  
  - **Target:** `postgres-pod` (namespace `default`)  
  - **Value:** `785`  
  - **Reasoning:** Operator‑directed kill of the blocking transaction (PID 785) on the Postgres pod to immediately release the row lock and unblock the waiting query (PID 771), as shown by the `get_blocking_transactions` evidence.

## Operator Review  
Approved without modification.  
*Operator feedback:* None.

## Execution Result  
- Action: Terminate backend PID 785 → **Result:** Success (`[SUCCESS] Terminated backend PID 785`).

## Follow-up Actions  
No further remediation required; the incident was resolved by terminating the blocking transaction. Continue to monitor for similar lock‑wait alerts.