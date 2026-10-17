# Incident Report: PodMemoryNearLimit

| Field | Value |
|---|---|
| Alert | PodMemoryNearLimit |
| Target | backend-deployment-b4488884b-6tmrb |
| Severity label | warning |
| Incident start (UTC) | 2026-10-17T15:43:57.893953+00:00 |
| Report generated (UTC) | 2026-10-17T15:44:01.622315+00:00 |
| Duration (alert to report) | 3s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary  
The **PodMemoryNearLimit** alert fired for pod `backend-deployment-b4488884b-6tmrb`. Investigation showed that memory usage grew from 28 MiB to 399 MiB in step with the request rate increase from 0.76 req/s to 2.76 req/s, indicating genuine load‑driven demand rather than a leak. No remediation actions were executed and the alert remained unresolved.

## Root Cause  
- The pod’s memory usage increased proportionally with the request rate, showing a genuine load‑driven demand. **Confidence:** high. (Supported by the memory and request‑rate trends.)  

## Supporting Evidence  
- Request rate (req/s): first = 0.76, last = 2.76, min = 0.76, max = 2.76.  
- Memory usage: first = 28 MiB, last = 399 MiB, min = 25 MiB, max = 399 MiB.  
- Unbounded buffers (leak indicator): first = 0.00, last = 0.00, min = 0.00, max = 0.00.  
- Bounded cache entries (genuine‑load indicator): first = 0.00, last = 360.00, min = 0.00, max = 360.00.  
- Root‑cause statement from investigation: “The pod’s memory usage is increasing in step with a rise in request rate, indicating genuine load‑driven demand rather than a memory leak.”  

## Proposed Remediation  
No remediation actions were proposed.

## Operator Review  
Operator feedback: **None**  

## Execution Result  
No actions were executed.

## Follow-up Actions  
Not available.