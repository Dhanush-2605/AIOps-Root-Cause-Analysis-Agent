# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T15:18:27.889856+00:00 |
| Report generated (UTC) | 2026-10-17T15:18:37.019325+00:00 |
| Duration (alert to report) | 9s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The **PostgresLockWaitersDetected** alert was triggered due to a row‑level lock contention on the `lock_test` table. Investigation identified a long‑running transaction (PID 1999) holding the lock, which blocked another transaction (PID 1989). No remediation action was executed; the situation remained unchanged at the end of the investigation.

## Root Cause  
A long‑running transaction (PID 1999) held a row lock on `lock_test` while executing an `UPDATE` statement, causing a lock wait for the concurrent `UPDATE` issued by PID 1989.  
**Confidence:** high (as stated in the investigation finding).

## Supporting Evidence  
- `get_blocking_transactions` returned:  
  - **blocked_pid:** 1989  
  - **blocked_query:** `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`  
  - **blocking_pid:** 1999  
  - **blocking_query:** `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`  
  - **blocking_duration:** `0:00:59.143311` (≈ 59 seconds)  
- The root‑cause statement explicitly links the blocking transaction to the observed lock waiters.  

## Proposed Remediation  
No remediation actions were proposed. The AI system determined that no safe automatic action could be taken, and the proposed plan JSON contained an empty `proposed_actions` list.

## Operator Review  
**Feedback:** None (operator did not provide any feedback).  

## Execution Result  
No actions were executed.

## Follow‑up Actions  
1. **Verify the blocking transaction:**  
   ```sql
   SELECT pid, query, state, now() - xact_start AS duration
   FROM pg_stat_activity
   WHERE pid = 1999;
   ```  
2. **If the transaction can be safely terminated, end it:**  
   ```sql
   SELECT pg_terminate_backend(1999);
   ```  
   *Rationale:* Terminating PID 1999 will release the row lock and allow PID 1989 to proceed.  
3. **If termination is not safe, monitor until completion:**  
   ```sql
   SELECT pid, now() - xact_start AS elapsed
   FROM pg_stat_activity
   WHERE pid = 1999;
   ```  
   *Rationale:* Wait for the transaction to finish naturally if it is performing critical work.  
4. **Post‑resolution verification:**  
   ```sql
   SELECT * FROM pg_locks WHERE relation = 'lock_test'::regclass;
   ```  
   Ensure no lingering locks remain on `lock_test`.  
5. **Alert tuning (preventive):**  
   - Review the threshold for `PostgresLockWaitersDetected` to reduce noise while still catching genuine contention.  
   - Consider adding a timeout or automatic termination policy for transactions exceeding a configurable duration (e.g., > 30 seconds).  

If the blocking transaction cannot be terminated and the lock persists, escalate to the database engineering team with the full query text and duration for deeper analysis.