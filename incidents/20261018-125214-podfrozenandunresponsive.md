# Incident Report: PodFrozenAndUnresponsive

| Field | Value |
|---|---|
| Alert | PodFrozenAndUnresponsive |
| Target | frozen-pod |
| Severity label | critical |
| Incident start (UTC) | 2026-10-18T12:50:27.900066+00:00 |
| Report generated (UTC) | 2026-10-18T12:52:12.247145+00:00 |
| Duration (alert to report) | 1m 44s |
| Outcome | Remediation executed |

---

## Summary  
The `PodFrozenAndUnresponsive` alert fired for the pod **frozen‑pod** because the pod was in the *Running* phase while its sole container was reported as not ready. Investigation showed the container was not ready despite no hanging requests, no error logs, and no blocking transactions. The pod was deleted, which succeeded and cleared the alert.

## Root Cause  
The container readiness flag was false (`ready: false`) while the pod remained in the *Running* phase, indicating a likely readiness‑probe failure. Confidence is **medium** because the readiness‑probe failure is inferred from the readiness flag without direct probe‑failure logs or metrics.

## Supporting Evidence  
- `get_pod_status` returned `"ready": false` for container **frozen-pod** while `"phase": "Running"` (Kubernetes API).  
- `fetch_filtered_pod_logs` reported **no error, timeout, hang or abort lines** in the last 300 log lines.  
- `fetch_hanging_requests` returned `"count": 0` and an empty request list.  
- `get_blocking_transactions` found **no blocking transactions**.  
- `get_deployment_status` returned **404 Not Found** for deployment `kube-state-metrics`, so the pod’s deployment object could not be inspected.  
- Execution log: `[SUCCESS] Deleted pod 'frozen-pod' in 'default'`.

## Proposed Remediation  
- **Action:** `delete_pod`  
  - **Target:** `frozen-pod`  
  - **Namespace:** `default`  
  - **Value:** *None*  
  - **Reasoning:** Deleting the pod forces recreation, which often resolves transient readiness‑probe failures when no deployment object is available for a controlled restart. Lower‑impact actions (e.g., killing blocking transactions) were not applicable.

## Operator Review  
Approved without modification.

## Execution Result  
- Deleted pod `frozen-pod` in namespace `default` – **Success** (as recorded in the execution log).

## Follow‑up Actions  
1. Retrieve the full pod specification (`kubectl get pod frozen-pod -n default -o yaml`) and inspect the `readinessProbe` configuration.  
2. If a deployment exists, locate it (`kubectl get deployment -A | grep frozen-pod`) and verify the probe settings in the deployment spec.  
3. Enable or collect readiness‑probe metrics (e.g., `kube_pod_container_status_ready` change events) to provide direct evidence of probe failures in future incidents.  
4. Add a monitoring rule to alert on consecutive readiness‑probe failures before the pod reaches the alert condition.  
5. Document the correct readiness‑probe configuration for the application to prevent similar mismatches.  
6. Review alert thresholds for `PodFrozenAndUnresponsive` to ensure they align with expected probe behavior.  

If any of the above steps encounter errors or missing resources, capture the exact error messages and command outputs for further analysis.