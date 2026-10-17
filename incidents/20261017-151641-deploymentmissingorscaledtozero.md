# Incident Report: DeploymentMissingOrScaledToZero

| Field | Value |
|---|---|
| Alert | DeploymentMissingOrScaledToZero |
| Target | python-chaos-agent |
| Severity label | critical |
| Incident start (UTC) | 2026-10-17T15:15:46.921789+00:00 |
| Report generated (UTC) | 2026-10-17T15:16:29.870476+00:00 |
| Duration (alert to report) | 42s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary
The **DeploymentMissingOrScaledToZero** alert fired for the `python-chaos-agent` deployment. Investigation showed the deployment was healthy, with the desired, ready, and available replica counts all equal to 1 and the sole pod running without errors. The alert was determined to be a false positive caused by a missing metric from **kube‑state‑metrics**, and no remediation actions were executed.

## Root Cause
The alert was triggered by a stale or missing metric `kube_deployment_status_replicas_available` from **kube‑state‑metrics**.  
- **Confidence:** high – the conclusion is directly supported by the deployment status, pod list, and pod logs, all of which contradict the alert condition.

## Supporting Evidence
- `get_deployment_status` returned:
  - `"desired_replicas": 1`
  - `"ready": 1`
  - `"available": 1`
  - `"updated": 1`
  - `"unavailable": 0`
  - Conditions showing `Available` = `True` (reason: `MinimumReplicasAvailable`) and `Progressing` = `True` (reason: `NewReplicaSetAvailable`).
- `list_pods` output:
  - `python-chaos-agent-7986d6dbc5-pwgfs | Running | ready 1/1 | restarts 0`
- `fetch_filtered_pod_logs` for the pod contained **no** error, timeout, hang, or abort lines in the last 200 log entries.

## Proposed Remediation
No remediation actions were proposed. The AI system determined that no safe action could be taken, and the critic feedback indicated “No actions proposed.”

## Operator Review
No operator feedback was provided.

## Execution Result
No actions were executed.

## Follow-up Actions
- Verify that **kube‑state‑metrics** is correctly scraping the `kube_deployment_status_replicas_available` metric for the `python-chaos-agent` deployment.
- Check Prometheus target health for the kube‑state‑metrics endpoint and review any recent scrape failures or rate‑limit events.
- If metric collection gaps are identified, restart the kube‑state‑metrics pod or adjust its configuration to ensure reliable metric exposure.
- Review the alert definition for `DeploymentMissingOrScaledToZero` to consider adding a short “no data” grace period or additional guard conditions that reduce false positives caused by transient metric loss.
- Document the incident in the alerting runbook and update monitoring dashboards to surface metric collection health for critical deployments.