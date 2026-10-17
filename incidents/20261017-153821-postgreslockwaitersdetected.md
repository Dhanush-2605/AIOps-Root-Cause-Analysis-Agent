# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T15:37:57.902449+00:00 |
| Report generated (UTC) | 2026-10-17T15:38:18.495050+00:00 |
| Duration (alert to report) | 20s |
| Outcome | Remediation executed |

---

## Summary  
The **PostgresLockWaitersDetected** alert was triggered because a row lock on `lock_test` prevented a second transaction from completing. Investigation identified that transaction **PID 2159** held the lock for over a minute, blocking **PID 2173**. The blocking transaction was terminated, releasing the lock and clearing the alert.

## Root Cause  
- Diagnosis: Transaction **PID 2159** was holding a row lock on `lock_test` for > 1 minute, directly blocking transaction **PID 2173** which attempted the same `UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1;`.  
- Confidence: **high** (the blocking transaction was confirmed by `get_blocking_transactions`).

## Supporting Evidence  
- `get_blocking_transactions` output:  

  ```
  blocked_pid | blocked_query | blocking_pid | blocking_query | blocking_duration
  2173        | UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1; | 2159 | UPDATE lock_test SET value = 'updated by terminal 2' WHERE id = 1; | 0:01:29.402287
  ```  

- Investigation finding line: **Error: PostgreSQL queries blocked by locks (PostgresLockWaitersDetected)**  
- Execution log: **[SUCCESS] Terminated backend PID 2159**

## Proposed Remediation  
| Action | Target | Namespace | Value | Reasoning |
|--------|--------|-----------|-------|-----------|
| kill_blocking_transaction | postgres | default | 2159 | Operator‑directed action: A blocking transaction (PID 2159) is holding a row lock on `lock_test` for over a minute, causing another transaction (PID 2173) to wait. Killing the blocking transaction will release the lock and resolve the lock waiters alert. |

## Operator Review  
**Approved without modification** – operator feedback: **None**.

## Execution Result  
- **kill_blocking_transaction** – succeeded; terminated backend PID 2159.

## Follow‑up Actions  
- Continue monitoring the **PostgresLockWaitersDetected** alert for recurrence.  
- Review application patterns that generate long‑running `UPDATE lock_test` transactions to reduce the likelihood of future lock contention.  
- No further immediate remediation is required.