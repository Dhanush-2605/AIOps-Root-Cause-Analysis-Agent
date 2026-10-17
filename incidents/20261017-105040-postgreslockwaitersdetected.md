# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T10:49:57.887772+00:00 |
| Report generated (UTC) | 2026-10-17T10:50:26.290728+00:00 |
| Duration (alert to report) | 28s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary
The PostgresLockWaitersDetected alert was triggered due to lock contention on the `employee_data` table. Investigation revealed multiple concurrent `SELECT … FOR UPDATE` statements causing transactions with PIDs 1939, 1940, 1941 to be blocked for up to 2 minutes by other transactions (e.g., PID 1899). No remediation action was executed; the incident remained unresolved.

## Root Cause
Multiple concurrent `SELECT … FOR UPDATE` statements on the `employee_data` table caused row‑level lock contention.  
**Confidence:** high – direct evidence from the `get_blocking_transactions` tool shows specific blocked and blocking PIDs and durations.

## Supporting Evidence
- `get_blocking_transactions` output:
  - PID 1939 blocked by PID 1940 for **0:01:10.192779**.
  - PID 1940 blocked by PID 1899 for **0:02:14.007862**.
  - PID 1941 blocked by PID 1940 for **0:01:10.192779**.
  - PID 1941 blocked by PID 1939 for **0:01:10.097154**.
- All blocked and blocking queries are identical: `SELECT * FROM employee_data WHERE id = $1 FOR UPDATE`.
- Blocking durations range from ~1 minute to over 2 minutes.

## Proposed Remediation
*No actions were proposed in the original plan.*  

## Operator Review
Operator feedback: **None**.

## Execution Result
No actions were executed.

## Follow-up Actions
1. **Terminate blocking transactions**  
   - Run `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid IN (1899, 1940, 1939, 1941);` to release the locks.  
   - Verify that the lock waiters are cleared after termination.

2. **Review application logic**  
   - Identify code paths that issue `SELECT … FOR UPDATE` on `employee_data`.  
   - Assess whether the `FOR UPDATE` clause is required or can be replaced with a less restrictive lock (e.g., `SELECT …` without `FOR UPDATE` or using `SELECT … FOR SHARE`).  

3. **Add concurrency controls**  
   - Implement application‑level throttling or queuing for operations that acquire row‑level locks on `employee_data`.  

4. **Improve monitoring**  
   - Create a dashboard that tracks lock wait times and the number of concurrent `SELECT … FOR UPDATE` statements on critical tables.  
   - Adjust the PostgresLockWaitersDetected alert thresholds if necessary to reduce noise while still catching high‑impact contention.  

5. **Post‑mortem documentation**  
   - Record the incident details, including the blocked/blocking PID list and durations, in the knowledge base for future reference.  

On‑call engineers should execute the termination command, then validate that normal query performance resumes before implementing longer‑term code or configuration changes.