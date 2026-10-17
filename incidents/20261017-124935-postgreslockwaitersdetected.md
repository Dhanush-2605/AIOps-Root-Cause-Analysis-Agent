# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T12:49:27.888701+00:00 |
| Report generated (UTC) | 2026-10-17T12:49:31.984632+00:00 |
| Duration (alert to report) | 4s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The PostgresLockWaitersDetected alert fired because queries were blocked waiting for row/table locks. Investigation identified that a PostgreSQL transaction (PID 785) held a row lock on `lock_test` for over a minute, blocking another transaction (PID 771) that attempted the same `UPDATE`. No remediation action was executed; the alert remained active.

## Root Cause  
- A PostgreSQL transaction (PID 785) was holding a row lock on `lock_test` for **0:01:09.260492**, blocking another transaction (PID 771). **Confidence: high** – based on the `get_blocking_transactions` finding.  

## Supporting Evidence  
- `get_blocking_transactions` returned:  
  - **blocked_pid:** 771  
  - **blocked_query:** `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`  
  - **blocking_pid:** 785  
  - **blocking_query:** `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`  
  - **blocking_duration:** `0:01:09.260492`  
- Alert type: **PostgresLockWaitersDetected**.  
- Root‑cause statement in the investigation notes confirming lock contention.

## Proposed Remediation  
Not available.

## Operator Review  
No feedback was provided by the operator.

## Execution Result  
No actions were executed.

## Follow-up Actions  
- Verify the current lock state:  
  ```sql
  SELECT pid, locktype, mode, granted, query
  FROM pg_locks l
  JOIN pg_stat_activity a ON l.pid = a.pid
  WHERE relation = 'lock_test'::regclass;
  ```  
- If PID 785 is still holding the lock and the blocked transaction (PID 771) cannot proceed, terminate the blocker:  
  ```sql
  SELECT pg_terminate_backend(785);
  ```  
- After termination, confirm that the blocked transaction (PID 771) resumes successfully.  
- Document the occurrence and consider implementing a lock timeout or query retry logic to reduce future impact.  
- Review alert thresholds for `PostgresLockWaitersDetected` to ensure they align with operational expectations.  