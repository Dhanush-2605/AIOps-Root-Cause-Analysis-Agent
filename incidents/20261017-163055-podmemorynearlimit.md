# Incident Report: PodMemoryNearLimit

| Field | Value |
|---|---|
| Alert | PodMemoryNearLimit |
| Target | mem-hog |
| Severity label | warning |
| Incident start (UTC) | 2026-10-17T16:30:27.897022+00:00 |
| Report generated (UTC) | 2026-10-17T16:30:33.101513+00:00 |
| Duration (alert to report) | 5s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The **PodMemoryNearLimit** alert fired for the pod **`mem-hog`**. Investigation revealed that essential memory‑usage and request‑rate metrics were missing, and logs showed no error, timeout, hang, or abort messages. No remediation could be applied; the incident remained unresolved.

## Root Cause  
The alert could not be correlated to a concrete failure because **observability data was missing**.  
- **Confidence:** low – the diagnosis is based on the absence of metric data rather than positive evidence of a leak or load‑driven demand.

## Supporting Evidence  
- `get_memory_trend` returned **NO DATA** for:  
  - Memory limit  
  - Request rate (req/s)  
  - Memory usage  
  - Unbounded buffers (leak indicator)  
  - Bounded cache entries (genuine‑load indicator)  
- `list_pods` output showed the target pod was **Running**, ready **1/1**, with **0 restarts**:  

  ```
  mem-hog | Running | ready 1/1 | restarts 0
  ```
- `fetch_filtered_pod_logs` for **`mem-hog`** reported **no error, timeout, hang or abort lines** in the last 200 log lines.  

- No other diagnostics (e.g., blocking DB transactions, idle‑in‑transaction sessions) were relevant to this memory‑usage alert.

## Proposed Remediation  
| Target | Action | Reasoning |
|--------|--------|-----------|
| Prometheus scrape configuration for the `mem-hog` pod | Verify that the pod’s container exposes `container_memory_working_set_bytes` and any request‑rate metrics, and that the Prometheus `scrape_config` includes the pod’s endpoint. | Restoring the missing metrics will allow `get_memory_trend` to return data, enabling accurate assessment of memory usage. |
| Pod instrumentation | Ensure that the application or side‑car exporter emits request‑rate metrics required by `get_memory_trend`. | Without request‑rate data the alert cannot distinguish between genuine load and a leak. |
| Monitoring validation | After fixing metric exposure, trigger a manual `get_memory_trend` query for `mem-hog` and confirm non‑empty results. | Confirms that the observability gap has been closed before re‑enabling the alert. |

## Operator Review  
No operator feedback was provided.

## Execution Result  
No actions were executed.

## Follow‑up Actions  
1. **Restore missing metrics**  
   - Run `kubectl describe pod mem-hog` to locate the container’s metrics endpoint.  
   - Check the Prometheus `scrape_config` for the pod’s namespace and ensure the target matches the endpoint.  
   - If the endpoint is missing, add the appropriate exporter or update the container to expose `container_memory_working_set_bytes` and request‑rate metrics.  

2. **Validate metric collection**  
   - Execute `curl <pod-metrics-endpoint>/metrics` from a node to confirm the metrics are being served.  
   - Run `promtool query instant --query='container_memory_working_set_bytes{pod="mem-hog"}'` to verify Prometheus is ingesting the data.  

3. **Re‑evaluate the alert**  
   - After metrics are confirmed, re‑run `get_memory_trend` for `mem-hog`.  
   - If the alert fires again, analyze the returned memory trend to determine whether the usage is load‑driven or indicative of a leak.  

4. **Document metric gaps**  
   - Update the observability checklist to include verification of memory and request‑rate metrics for all pods that trigger `PodMemoryNearLimit` alerts.  

These steps should close the observability gap and enable accurate root‑cause analysis for future memory‑related alerts.