# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T12:02:57.889186+00:00 |
| Report generated (UTC) | 2026-10-17T12:03:58.195822+00:00 |
| Duration (alert to report) | 1m 0s |
| Outcome | Remediation executed |

---

## Summary
The **PostgresLockWaitersDetected** alert was triggered due to a row lock on `lock_test` that blocked query execution. Investigation identified a long‑running transaction (PID 293) holding the lock for over 10 minutes, which blocked another transaction (PID 395). An attempt to terminate the blocking transaction was made, but the termination command failed.

## Root Cause
A long‑running transaction (PID 293) held a row lock on `lock_test` for **0:10:24.888146** (≈10 minutes), preventing other queries from acquiring the lock.  
**Confidence:** high – based on the `get_blocking_transactions` finding that directly shows PID 293 blocking PID 395.

## Supporting Evidence
- `get_blocking_transactions` returned:  
  - **blocked_pid:** 395  
  - **blocked_query:** `UPDATE lock_test SET value = 'updated by terminal 1' WHERE id = 1;`  
  - **blocking_pid:** 293  
  - **blocking_query:** `UPDATE lock_test SET value = 'updated by terminal 1' WHERE id = 1;`  
  - **blocking_duration:** `0:10:24.888146`
- Alert: **PostgresLockWaitersDetected**.
- Execution log:  
  `[ERROR] Execution failed for postgres: pg_terminate_backend(293) did not return true (got: 'true'); PID may be gone`

## Proposed Remediation
- **Action:** `kill_idle_transaction`  
  - **Target:** `postgres` (namespace `default`)  
  - **Value:** `293`  
  - **Reasoning:** Operator‑directed action to kill the blocking transaction (PID 293) that has held a row lock for over 10 minutes, as identified by `get_blocking_transactions`. This is intended to release the lock and unblock the waiting query.

## Operator Review
Approved without modification.  
*Critic feedback:* “Approved. The proposed kill_idle_transaction action targets PID 293, which is the blocking transaction identified in the evidence. The value is a valid numeric PID, and the action is appropriate and low‑impact. No disallowed actions are present.”  
*Operator feedback:* None.

## Execution Result
- **kill_idle_transaction (PID 293):**  
  `[ERROR] Execution failed for postgres: pg_terminate_backend(293) did not return true (got: 'true'); PID may be gone`

## Follow-up Actions
1. Verify whether PID 293 still exists:  
   ```sql
   SELECT pid, state, query FROM pg_stat_activity WHERE pid = 293;
   ```
2. If the PID is still present, attempt termination again and capture the result:  
   ```sql
   SELECT pg_terminate_backend(293);
   ```
3. Confirm that the lock on `lock_test` has been released:  
   ```sql
   SELECT * FROM pg_locks WHERE relation = 'lock_test'::regclass;
   ```
4. If the lock persists, consider restarting the PostgreSQL instance or manually rolling back the transaction after confirming no critical work is in progress.
5. Review application code to ensure transactions on `lock_test` are bounded in duration and include explicit commit/rollback logic.
6. Adjust alert thresholds or add additional monitoring to detect long‑running transactions earlier, reducing the window for lock contention.