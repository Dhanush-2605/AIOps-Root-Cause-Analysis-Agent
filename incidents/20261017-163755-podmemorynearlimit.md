# Incident Report: PodMemoryNearLimit

| Field | Value |
|---|---|
| Alert | PodMemoryNearLimit |
| Target | backend-deployment-5bb4c59f9d-x9sz8 |
| Severity label | warning |
| Incident start (UTC) | 2026-10-17T16:35:27.887666+00:00 |
| Report generated (UTC) | 2026-10-17T16:37:51.786745+00:00 |
| Duration (alert to report) | 2m 23s |
| Outcome | Remediation executed |

---

## Summary
The `PodMemoryNearLimit` alert fired for pod `backend-deployment-5bb4c59f9d-x9sz8` as its memory usage approached the configured limit of 512 MiB. Investigation showed the increase was driven by higher request rates and growth of a bounded cache, not by a memory leak. The memory limit was increased to 522 MiB, resolving the alert.

## Root Cause
- The pod’s memory usage rose in step with the request rate (from 0.51 req/s to 3.00 req/s) and the bounded cache entries (up to 375) – **Finding:** `Memory` and `Bounded cache entries` trends.  
- No unbounded buffers were observed, indicating the growth was not due to a leak – **Finding:** `Unbounded buffers` remained at 0.00.  
- Confidence in this diagnosis was reported as **high**.

## Supporting Evidence
- Memory limit remained constant: first = 512 MiB, last = 512 MiB, min = 512 MiB, max = 512 MiB.  
- Request rate increased: first = 0.51 req/s, last = 3.00 req/s, min = 0.51 req/s, max = 3.00 req/s.  
- Memory usage grew: first = 8 MiB, last = 398 MiB, min = 8 MiB, max = 414 MiB.  
- Unbounded buffers (leak indicator) stayed at 0.00 throughout.  
- Bounded cache entries (genuine‑load indicator) grew to 375.00 entries.  
- No evidence gaps were identified.

## Proposed Remediation
- **Action:** increase_memory_limit  
  - **Target:** `backend-deployment` (namespace `default`)  
  - **Value:** `522Mi` (increase by 10 Mi)  
  - **Reasoning:** Operator directed to increase the memory limit by 10 Mi to accommodate the observed genuine load‑driven memory demand (memory usage rose with request rate and bounded cache growth).

## Operator Review
Approved without modification.  
Operator feedback: “just increase 10mb of memory”.

## Execution Result
- **Action:** Increase memory limit for `backend-deployment` (container: `backend`) to `522Mi` – **Result:** `[SUCCESS] Increased memory limit for 'backend-deployment' (container: backend) to 522Mi`.

## Follow-up Actions
- Continue monitoring memory usage, request rate, and bounded cache size to ensure the increased limit remains sufficient.  
- Review alert thresholds periodically to confirm they remain appropriate for genuine load‑driven demand.