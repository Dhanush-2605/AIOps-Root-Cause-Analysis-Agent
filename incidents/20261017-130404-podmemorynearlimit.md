# Incident Report: PodMemoryNearLimit

| Field | Value |
|---|---|
| Alert | PodMemoryNearLimit |
| Target | backend-deployment-55d76847dd-nzfzk |
| Severity label | warning |
| Incident start (UTC) | 2026-10-17T13:03:57.896408+00:00 |
| Report generated (UTC) | 2026-10-17T13:04:01.910546+00:00 |
| Duration (alert to report) | 4s |
| Outcome | Not remediated (operator aborted, or no safe action was proposed) |

---

## Summary
The `PodMemoryNearLimit` alert fired for pod `backend-deployment-55d76847dd-nzfzk`. Investigation showed that memory usage increased in step with request rate, indicating genuine load‑driven demand rather than a leak. No remediation actions were executed and the alert remained unaddressed by automation.

## Root Cause
The memory increase was caused by genuine load‑driven demand. **Confidence: high** – this conclusion is based on the observed correlation between request rate and memory usage, the absence of unbounded buffers, and the stable bounded cache entry count.

## Supporting Evidence
- Request rate (req/s): first = 0.29, last = 3.00, min = 0.29, max = 3.00.  
- Memory usage: first = 26 MiB, last = 417 MiB, min = 26 MiB, max = 417 MiB.  
- Unbounded buffers (leak indicator): first = 0.00, last = 0.00, min = 0.00, max = 0.00.  
- Bounded cache entries (genuine‑load indicator): first = 0.00, last = 360.00, min = 0.00, max = 360.00.  
- The request rate grew from 0.29 req/s to 3.00 req/s over the last 15 minutes while memory grew from 26 MiB to 417 MiB.

## Proposed Remediation
*No actions were proposed.* The plan JSON contained an empty `proposed_actions` list and required human approval.

## Operator Review
No feedback was provided by the operator.  
> Operator feedback: None

## Execution Result
No actions were executed.

## Follow-up Actions
Not available.