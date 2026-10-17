# Incident Report: PostgresLockWaitersDetected

| Field | Value |
|---|---|
| Alert | PostgresLockWaitersDetected |
| Target | UnknownResource |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T11:19:27.888351+00:00 |
| Report generated (UTC) | 2026-10-17T11:27:46.298857+00:00 |
| Duration (alert to report) | 8m 18s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The **PostgresLockWaitersDetected** alert fired, indicating that at least one PostgreSQL query was waiting on a lock. Investigation was unable to connect to `pgpool-service:5432`, resulting in time‑outs for both `get_blocking_transactions` and `get_idle_in_transaction` diagnostics, and pod logs showed DNS‑resolution failures for the replica service. No remediation actions were executed; the incident was aborted by a human operator.

## Root Cause  
- **Immediate cause:** The Prometheus metric `pg_stat_activity_count{wait_event_type="Lock"}` was > 0, confirming the presence of lock waiters. *(Final Summary – Root Cause)*  
- **Contributing factor:** Diagnostic queries (`get_blocking_transactions`, `get_idle_in_transaction`) failed with time‑outs, preventing identification of the specific blocking session. *(Investigation findings – get_blocking_transactions / get_idle_in_transaction)*  
- **Confidence:** **Low** – the lock‑wait condition is confirmed, but the exact blocker cannot be pinpointed due to missing diagnostic data and unrelated DNS‑resolution errors in the logs. *(Final Summary – Confidence)*  

## Supporting Evidence  
- Alert: **PostgresLockWaitersDetected** (input header).  
- Prometheus metric `pg_stat_activity_count{wait_event_type="Lock"}` > 0 indicating lock waiters. *(Final Summary – Root Cause)*  
- `get_blocking_transactions`: *Error executing diagnostic query: Could not connect/query pgpool-service:5432: connection to server at "pgpool-service" (10.0.14.205), port 5432 failed: timeout expired*  
- `get_idle_in_transaction`: *Error executing diagnostic query: Could not connect/query pgpool-service:5432: connection to server at "pgpool-service" (10.0.14.205), port 5432 failed: timeout expired*  
- Pod list:  
  - `postgres-0 | Running | ready 1/1 | restarts 0`  
  - `postgres-1 | Running | ready 1/1 | restarts 0` *(list_pods)*  
- Log excerpts (last 200 lines):  
  - `2026-10-17 11:17:23.965 GMT [167] ERROR:  syntax error at or near "clear" at character 1` *(fetch_filtered_pod_logs)*  
  - Multiple `FATAL:  could not connect to the primary server: could not translate host name "postgres-0.postgres-headless" to address: Name or service not known` entries at `06:58:59.950 GMT` through `10:59:43.150 GMT`. *(fetch_filtered_pod_logs)*  
- No “Request exceeded hang threshold” or other direct lock‑holder logs.  
- Execution log: **[ABORTED BY HUMAN OPERATOR] No actions executed.**  

## Proposed Remediation  
- **Action:** `restart_deployment`  
  - **Target:** `postgres` (namespace `default`)  
  - **Value:** *Not available*  
  - **Reasoning:** *Operator-directed action: restart the PostgreSQL deployment to clear any unknown lock holder causing the lock waiters. The alert shows a blocked query but diagnostics could not identify the blocking transaction due to connectivity timeouts, so a restart is the lowest‑impact way to reset the state.* *(Proposed plan – reasoning)*  

## Operator Review  
**Feedback:** *None* (Operator feedback: None).  

## Execution Result  
No actions were executed.

## Follow‑up Actions  
1. **Restore database connectivity**  
   - Verify DNS resolution for `postgres-0.postgres-headless` from within the pod (`kubectl exec -it <pod> -- nslookup postgres-0.postgres-headless`).  
   - Check Service and Endpoints objects for `postgres-headless` (`kubectl get svc,ep -n default postgres-headless`).  
   - Review NetworkPolicy rules that might block traffic to the pod IP `10.0.14.205`.  

2. **Re‑run diagnostics once connectivity is confirmed**  
   - Execute `get_blocking_transactions` and `get_idle_in_transaction` again to capture the blocking session IDs.  

3. **If lock waiters persist after connectivity is restored**  
   - Identify the blocking transaction via `SELECT pid, query, state FROM pg_stat_activity WHERE wait_event_type = 'Lock';` on the primary.  
   - If a long‑running or idle‑in‑transaction session is holding the lock, terminate it with `SELECT pg_terminate_backend(<pid>);`.  

4. **Document any DNS or network misconfiguration** discovered during step 1 and apply corrective changes (e.g., update CoreDNS ConfigMap, fix Service selectors).  

5. **Review alert thresholds** for `pg_stat_activity_count{wait_event_type="Lock"}` to ensure they align with operational expectations and reduce false positives.  

6. **Post‑mortem**: Record the root cause once the blocking transaction is identified or the connectivity issue is resolved, and update runbooks accordingly.  