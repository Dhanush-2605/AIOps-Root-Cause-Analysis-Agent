# Future Scope

## Current limitations

- Alerts go straight into the agent with no queue, so a burst of alerts means a burst of LLM calls and token-limit errors.
- Agent state lives in RAM (`MemorySaver`, `triggered_threads`, `alert_feed`). A restart loses everything, and only one replica can run.
- The investigator gets the same hard-coded tool list for every alert.
- Logs are read live from the Kubernetes API, so they are gone once the pod is deleted.
- Past incidents are written to `incidents/` but never used again.


Ideas for anyone who wants to improve this project and make it more scalable.

## 1. Runbook prompts with a vector DB

Right now the investigator gets the same tools for every alert. Instead, store a runbook per alert in a vector DB. Each entry says which tools to call for that alert. When an alert comes in, fetch its runbook and give the investigator only those tool names.
To support a new alert, just add a new rule and list its tools. No agent code change.

Example entry:

```yaml
alert: PodMemoryNearLimit
required_tools: [get_memory_trend, get_pod_status, fetch_filtered_pod_logs]
hints:
  - memory rises with load and plateaus -> increase_memory_limit
  - buffers grow while load is flat -> restart_deployment
allowed_actions: [increase_memory_limit, restart_deployment]
```

## 2. Keep logs outside Kubernetes

We read logs from the Kubernetes API, which only works while the pod exists. If the pod is deleted or restarted, the agent has no context. Ship logs to a separate store in a fixed format so the agent can always read them.

## 3. Alerts and tools per environment

Different apps and environments need different custom alerts. Each one needs the right tools and a consistent schema, so they work the same way everywhere.

- Every alert should carry severity, service, env and a runbook reference.

- Tools should return structured output like {status: ok|no_data|error, data, evidence_gap} instead of free text.

## 4. Save approved solutions

When the operator approves a plan, save the problem and the fix in the vector DB. Next time a similar alert comes, the agent follows the same flow and skips unnecessary tool calls.

- Fetch the closest past incident, run one or two cheap tools to confirm it still matches, then propose the saved plan. If it doesn't match, do the full investigation. The existing reports in incidents/ can seed the DB.

## 5. Alert queue and background workers

Don't send alerts straight to the agent. Store them in a queue, and let background workers pick up 
as many as the agent can handle. This avoids token limit issues, gives the agent time to work, and lets us sort the queue so severe alerts go first.

## 6. Realistic DB lock scenario

Run the blocking transaction through API calls instead of two manual `psql` sessions.

- The backend already has `/api/orders/lock-row` and `/api/orders/checkout-slow`. Add a k6 script that calls lock-row once, then several concurrent checkout-slow calls.
- This raises two alerts, `PostgresLockWaitersDetected` and `HangingRequestsDetected`, for one root cause. Group alerts that arrive close together for the same service into one incident.
- Pick the primary alert by layer: database beats application. Aborting the hanging requests only treats the symptom, because the lock holder keeps blocking new requests. The plan should target the lock holder.
- Alertmanager `inhibit_rules` can mute the symptom alert while the cause alert is firing.


## 7. Distributed agent

State is stored in the RAM of one machine. If a thread is waiting for human approval, no other agent on another node can continue it. Use a central DB for the state so any free agent can pick up any thread.

- Lock each thread with a lease (Postgres advisory lock) so two workers never resume the same one. The executor re-runs from the top on resume, so actions must be idempotent: record "started/completed" per action and skip completed ones.
- Use a separate DB for agent state, not the application's Postgres, since the agent must still work when that one is broken.
- Partitioning by `thread_id` can come later. Start with table partitioning in one database and shard only if load requires it.


## 8. Slack or Teams approvals 

- with a timeout and escalation. Never auto-execute on timeout.



## 9. Tracing and metrics

- for the agent: tokens per incident, queue depth, approval rate, fix success rate.



