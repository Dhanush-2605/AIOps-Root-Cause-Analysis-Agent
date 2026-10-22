# Incident Report: CPUThrottlingHigh

| Field | Value |
|---|---|
| Alert | CPUThrottlingHigh |
| Target | aks-secrets-store-provider-azure-fx9nl |
| Severity label | warning |
| Incident start (UTC) | 2026-10-22T01:34:20.184063+00:00 |
| Report generated (UTC) | 2026-10-22T01:34:58.244958+00:00 |
| Duration (alert to report) | 38s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary
The **CPUThrottlingHigh** alert triggered for the pod `aks‑secrets‑store‑provider‑azure‑fx9nl`. Investigation revealed the pod was running (Ready 1/1, 0 restarts) but was being CPU‑throttled due to an insufficient CPU limit. No remediation was applied; the incident was aborted.

## Root Cause
The pod `aks‑secrets‑store‑provider‑azure‑fx9nl` exceeded its configured CPU limit, causing the Prometheus alert that the ratio of throttled CPU periods to total CPU periods was greater than 0.25.  
**Confidence:** medium – based on the alert definition and the pod’s running state with no log errors.

## Supporting Evidence
- Alert: `CPUThrottlingHigh` on target `aks‑secrets‑store‑provider‑azure‑fx9nl`.  
- `[get_pod_status]` returned **404 Not found** while reading status of pod `aks‑secrets‑store‑provider‑azure‑fx9nl`.  
- `[list_pods]` showed `aks‑secrets‑store‑provider‑azure‑fx9nl` in **Running** phase, **Ready 1/1**, **restarts 0**.  
- `[fetch_filtered_pod_logs]` reported **no error, timeout, hang or abort lines** in the last 200 log lines.  
- `[get_deployment_status]` returned **404 Not found** while reading deployment `aks‑secrets‑store‑provider‑azure`.  
- Root‑cause statement: “The pod `aks‑secrets‑store‑provider‑azure‑fx9nl` is being CPU‑throttled because its CPU limit is too low for the amount of work it is performing.”  
- Evidence gaps noted: missing pod CPU request/limit spec and missing actual CPU usage measurements.

## Proposed Remediation
Not available.

## Operator Review
Aborted – no action was taken.  
Operator feedback: **None**.

## Execution Result
No actions were executed.

## Follow‑up Actions
1. **Retrieve pod specification**  
   ```bash
   kubectl describe pod aks-secrets-store-provider-azure-fx9nl -n <namespace>
   ```  
   Verify the `cpu` request and limit values.

2. **Compare CPU usage to limits**  
   ```bash
   kubectl top pod aks-secrets-store-provider-azure-fx9nl -n <namespace>
   ```  
   Observe if actual CPU usage consistently exceeds the configured limit.

3. **Adjust CPU limits if necessary**  
   - Edit the deployment or pod spec to increase the CPU limit (e.g., from `200m` to `500m`).  
   - Apply the change:  
     ```bash
     kubectl apply -f <updated‑manifest>.yaml
     ```

4. **Monitor throttling metric**  
   - Verify the Prometheus metric `container_cpu_cfs_throttled_periods_total` vs. `container_cpu_cfs_periods_total` for the pod.  
   - Ensure the throttling ratio falls below the alert threshold (0.25).

5. **Document the change** in the change‑log and update any relevant runbooks.

6. **If limits cannot be increased** (e.g., due to node capacity), consider workload optimization or scaling the deployment.

These steps should confirm the CPU configuration, remediate the throttling, and prevent recurrence of the `CPUThrottlingHigh` alert.