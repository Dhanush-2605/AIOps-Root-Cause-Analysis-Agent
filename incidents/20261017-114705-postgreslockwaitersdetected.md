# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T11:46:27.903426+00:00 |
| Report generated (UTC) | 2026-10-17T11:46:50.107930+00:00 |
| Duration (alert to report) | 22s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The **PostgresLockWaitersDetected** alert fired, indicating at least one PostgreSQL session was waiting for a lock. Diagnostic queries (`get_blocking_transactions`, `get_idle_in_transaction`) failed to connect to `pgpool-service:5432` (timeout), and pod logs from `postgres-1` showed repeated FATAL errors related to DNS resolution of `postgres-0.postgres-headless`. No remediation actions were executed and the incident remained unresolved.

## Root Cause  
- The alert was triggered by the Prometheus query `sum(pg_stat_activity_count{wait_event_type="Lock"}) > 0`, confirming that a session was waiting for a lock. *(Finding: alert name and type)*  
- Direct diagnostics that could identify the blocking transaction could not run because the query to `pgpool-service:5432` timed out:  
  - `Error executing diagnostic query: Could not connect/query pgpool-service:5432: connection to server at "pgpool-service" (10.0.14.205), port 5432 failed: timeout expired` *(Finding: get_blocking_transactions & get_idle_in_transaction errors)*  
- Pod logs from `postgres-1` repeatedly reported connectivity failures to the primary server:  
  - `FATAL:  could not connect to the primary server: could not translate host name "postgres-0.postgres-headless" to address: Name or service not known`  
  - `FATAL:  could not receive data from WAL stream: server closed the connection unexpectedly` *(Finding: fetch_filtered_pod_logs)*  
- Because the blocking‑transaction diagnostics were unavailable, the exact session holding the lock could not be identified. The most plausible explanation, given the observed replication/DNS connectivity issues, is a lingering transaction that retained locks. **Confidence:** low (based on indirect evidence and missing diagnostic data).

## Supporting Evidence  
- Alert: `PostgresLockWaitersDetected` (Route: abort)  
- Diagnostic query failures:  
  - `Error executing diagnostic query: Could not connect/query pgpool-service:5432: connection to server at "pgpool-service" (10.0.14.205), port 5432 failed: timeout expired` (both `get_blocking_transactions` and `get_idle_in_transaction`)  
- Pod status list:  

  | name | phase | ready | restarts |
  |------|-------|-------|----------|
  | backend-deployment-567bf98dbd-h9vck | Running | ready 1/1 | 0 |
  | frontend-deployment-7999b898fb-4b8bg | Running | ready 1/1 | 0 |
  | pgpool-7f9f789549-xfkkz | Running | ready 1/1 | 0 |
  | postgres-0 | Running | ready 1/1 | 0 |
  | postgres-1 | Running | ready 1/1 | 0 |
  | python-chaos-agent-66dcb994bb-w2wf4 | Running | ready 1/1 | 0 |

- Log excerpts from `postgres-1`:  
  - `2026-10-17 06:58:59.950 GMT [104] FATAL:  could not connect to the primary server: could not translate host name "postgres-0.postgres-headless" to address: Name or service not known`  
  - `2026-10-17 10:59:33.130 GMT [109] FATAL:  could not receive data from WAL stream: server closed the connection unexpectedly`  
- No explicit “lock wait” messages were found in the logs.  

## Proposed Remediation  
Not available  

## Operator Review  
Feedback given:  
- **Critic feedback:** No actions proposed.  
- **Operator feedback:** None  

## Execution Result  
No actions were executed.

## Follow-up Actions  
Not available