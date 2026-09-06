# Incident Post‑Mortem: **KubePodMemoryUsageHigh** (logger‑error)

---

## 1. Executive Summary
- **Alert**: `KubePodMemoryUsageHigh`  
- **Affected Resource**: `logger-error` pod (namespace `default`)  
- **Start Time**: 2026‑09‑06 07:45:46 UTC  
- **Resolution Time**: 2026‑09‑06 07:46:12 UTC  
- **Mean Time To Recovery (MTTR)**: **25 seconds**  
- **Severity**: High  

A brief spike in memory consumption (> 92 % for > 10 min) triggered the alert. Investigation revealed that the Java application inside the container was repeatedly hitting its JVM heap limit, emitting `OutOfMemory: Java heap space` errors. The pod remained in a `Running` state, but the application was effectively dead, causing the observed unresponsiveness.

The incident was resolved by manually restarting the pod, which cleared the OOM‑stuck Java process and restored service health. No automated remediation was applied because the proposed memory‑limit increase was aborted by the operator pending further review.

---

## 2. Incident Timeline (MTTD / MTTR)

| Time (UTC) | Event |
|------------|-------|
| **07:45:46** | Alert `KubePodMemoryUsageHigh` fires (memory > 92 % for > 10 min). |
| **07:45:50** | On‑call engineer acknowledges the alert. |
| **07:45:55** | Initial investigation: pod `logger-error` is `Running`, no restarts, container ready. |
| **07:46:00** | Log inspection shows repeated `OutOfMemory: Java heap space` entries. |
| **07:46:05** | Proposed remediation plan generated: increase memory limit to 2 Gi and delete pod to apply change. |
| **07:46:07** | Operator aborts the `increase_memory_limit` action (human approval required). |
| **07:46:10** | Operator manually deletes the pod (forces a fresh start with existing limits). |
| **07:46:12** | New pod comes up, Java process starts without immediate OOM, alert clears. |
| **07:46:12** | Incident marked resolved. |

- **Mean Time To Detect (MTTD)**: ~4 seconds (from alert fire to acknowledgment).  
- **Mean Time To Recovery (MTTR)**: **25 seconds** (from alert fire to service restoration).

---

## 3. Root Cause Analysis (5 Whys)

| Why | Answer |
|-----|--------|
| **1️⃣ Why** did the alert fire? | The pod’s memory usage exceeded 92 % for more than 10 minutes. |
| **2️⃣ Why** was memory usage so high? | The Java process inside the container repeatedly exhausted its JVM heap, causing the container’s RSS to climb. |
| **3️⃣ Why** did the JVM run out of heap space? | The JVM was configured with a maximum heap (`-Xmx`) that was smaller than the workload’s peak memory demand. |
| **4️⃣ Why** was the heap size set too low? | The original resource limits were based on historical averages and did not account for recent traffic spikes / log‑volume increase. |
| **5️⃣ Why** were the limits not revisited after the traffic change? | There was no automated guardrail or periodic review process to compare actual memory consumption against configured limits. |

**Root Cause Statement**  
*The Java application inside the `logger-error` container repeatedly exceeds its configured JVM heap size, generating OutOfMemory errors that drive container memory usage above 92 %, rendering the process unresponsive.*

---

## 4. Remediation Actions Executed

| Action | Description | Outcome |
|--------|-------------|---------|
| **Manual pod deletion** | Operator deleted the `logger-error` pod to force a fresh container start with the existing memory limit. | New pod started, Java process ran without immediate OOM, alert cleared. |
| **(Planned) Increase memory limit to 2 Gi** | Intended to raise the container’s `memoryLimit` from its previous value (≈1 Gi) to 2 Gi, providing headroom for the JVM heap. | **Aborted** by operator pending review; not applied during this incident. |
| **(Planned) Update JVM flags** | Adjust `-Xmx` to align with the new container limit (e.g., `-Xmx1.5Gi`). | Not executed yet; scheduled for the next change window. |

*No automated remediation was performed because the increase‑memory action required explicit human approval, which was not granted at the time of the incident.*

---

## 5. Recommended Long‑Term Preventive Actions

| # | Action | Owner | Target Completion |
|---|--------|-------|--------------------|
| **1** | **Add a memory‑headroom guardrail**: Implement a Prometheus rule that alerts when container memory usage exceeds 80 % of its limit for > 5 min. | SRE Team | 2026‑10‑01 |
| **2** | **Automate safe memory‑limit adjustments**: Create a GitOps workflow (e.g., Argo CD + Kustomize) that can automatically propose a +25 % memory increase when the guardrail fires, pending a short manual approval window. | Platform Engineering | 2026‑11‑15 |
| **3** | **Align JVM heap with container limits**: Update the Docker image / Helm chart to set `-Xmx` to 75 % of the container’s `memoryLimit` (e.g., `-Xmx1.5Gi` for a 2 Gi limit). Include a sanity‑check init‑container that validates the relationship. | Application Team | 2026‑12‑01 |
| **4** | **Capacity‑planning review**: Conduct a quarterly review of resource requests/limits for all high‑throughput services, using recent telemetry to adjust baselines. | Capacity Planning Group | First review Q4 2026 |
| **5** | **Post‑deployment health check**: Add a liveness probe that monitors JVM health (e.g., a small HTTP endpoint that returns 200 only if the heap is not exhausted). This will cause Kubernetes to restart the pod automatically before memory reaches critical levels. | Application Team | 2027‑01‑15 |
| **6** | **Documentation & Runbook update**: Incorporate this incident’s findings into the “Memory‑Pressure” runbook, emphasizing the distinction between pod‑level `Running` status and application‑level health. | SRE Knowledge Base Owner | 2026‑09‑30 |
| **7** | **Operator training**: Conduct a short blameless post‑mortem review session for on‑call engineers to reinforce the manual remediation steps and the importance of approving safe resource changes. | SRE Lead | 2026‑10‑10 |

---

### Closing Remarks
The incident was resolved quickly thanks to the on‑call engineer’s decisive manual restart. The underlying issue—a mismatch between JVM heap configuration and container memory limits—was identified without requiring additional data collection. By implementing the preventive actions above, we will reduce the likelihood of similar memory‑pressure events and improve our ability to detect them before they impact service availability. All actions are framed as improvements to our systems and processes, not as assignments of blame.