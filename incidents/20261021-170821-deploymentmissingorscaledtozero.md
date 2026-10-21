# Incident Report: DeploymentMissingOrScaledToZero

| Field | Value |
|---|---|
| Alert | DeploymentMissingOrScaledToZero |
| Target | python-chaos-agent |
| Severity label | critical |
| Incident start (UTC) | 2026-10-20T16:58:50.191177+00:00 |
| Report generated (UTC) | 2026-10-21T17:08:18.426210+00:00 |
| Duration (alert to report) | 1449m 28s |
| Outcome | Remediation executed |

---

## Summary
The `DeploymentMissingOrScaledToZero` alert fired for the `python-chaos-agent` deployment, indicating zero available replicas. Investigation showed the deployment was healthy with one available replica, and the alert was caused by a stale `kube_deployment_status_replicas_available` metric. The deployment was restarted, after which the alert condition cleared.

## Root Cause
The alert was triggered by a stale or inaccurate `kube_deployment_status_replicas_available` metric, which reported 0 available replicas for `python-chaos-agent` despite the deployment actually having 1 available replica. **Confidence:** high – this diagnosis is directly supported by the deployment status and pod list findings.

## Supporting Evidence
- `get_deployment_status` reported `"available": 1`, `"ready": 1`, `"desired_replicas": 1`, and conditions showing `"type": "Available", "status": "True"`.
- `list_pods` showed a single pod `python-chaos-agent-854b758447-fpn5l` in **Running** phase, `ready 1/1`, with `restarts 0`.
- No evidence gaps were identified; the deployment status and pod list directly contradicted the alert’s condition.

## Proposed Remediation
- **Action:** `restart_deployment`  
  **Target:** `python-chaos-agent` (namespace `default`)  
  **Value:** *None*  
  **Reasoning:** Operator‑directed restart of the `python-chaos-agent` deployment to trigger a fresh pod rollout, causing kube‑state‑metrics to emit up‑to‑date replica counts. The deployment is healthy, and the alert was a false positive due to a stale metric.

## Operator Review
Approved without modification.  
*Operator feedback:* None

## Execution Result
- Restarted deployment `python-chaos-agent` in `default` – **SUCCESS**

## Follow-up Actions
- Review and adjust the alert definition for `DeploymentMissingOrScaledToZero` to include a check for metric staleness or to require multiple consecutive samples before firing.
- Verify the health and scrape interval of `kube-state-metrics` to reduce the likelihood of stale `kube_deployment_status_replicas_available` data.
- Add monitoring for metric freshness (e.g., `kube_state_metrics_last_scrape_success`) and create a low‑severity alert if metrics become stale.
- Document the incident in the runbook and include the steps taken to restart the deployment as a standard remediation for similar false‑positive alerts.