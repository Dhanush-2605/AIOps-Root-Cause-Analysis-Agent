import json
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt
from langgraph.checkpoint.memory import MemorySaver
from kubernetes.stream import stream

from agent_state import AgentState, RemediationPlan, CriticReview
from agent_tools import INVESTIGATION_TOOLS, get_gke_client, run_psql
from github_utils import commit_post_mortem_to_repo
from model import llm


tools = INVESTIGATION_TOOLS  # all read-only diagnostics
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

SOP_CONTEXT = ""  # Put cluster SOPs here; they are given to the critic.

MAX_INVESTIGATION_ITERATIONS = 6
MAX_CRITIC_ATTEMPTS = 2          # critic may bounce a plan back to the proposer this many times
MAX_SCALE_REPLICAS = 10          # executor-side guard; scale_deployment outside 1..N is refused
APPROVE_WORDS = {"approve", "approved", "y", "yes", "execute"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get(obj, key: str, default=None):
    """Read a field from a Pydantic object OR a dict (checkpoints may hand back dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _alert_name(alert: dict) -> str:
    return (
        alert.get("alertname")
        or alert.get("labels", {}).get("alertname")
        or alert.get("alert_name")
        or "unknown"
    )


def _compact(obj) -> str:
    """Compact JSON (no indentation) — indentation only burns input tokens."""
    return json.dumps(obj, separators=(",", ":"), default=str)


def _plan_json(plan) -> str:
    if plan is None:
        return "{}"
    if hasattr(plan, "model_dump_json"):
        return plan.model_dump_json()
    return _compact(plan)


def _empty_plan(reason: str) -> RemediationPlan:
    return RemediationPlan(
        root_cause=reason,
        severity="Low",
        confidence="inconclusive",
        proposed_actions=[],
        requires_human_approval=True,
    )


# ---------------------------------------------------------------------------
# 1. Investigator
# ---------------------------------------------------------------------------
INVESTIGATOR_SYS_MSG = SystemMessage(content="""You are an SRE diagnostic agent. Investigate and identify the root cause only; never remediate.

Rules:
1. Before concluding "traffic spike" or "needs more resources", check for resource hoarding (blocking queries, idle-in-transaction connections). Saturation is a symptom until proven otherwise.
2. For memory alerts, tell a leak from genuine load using get_memory_trend:
   - Leak: unbounded buffers keep growing, or memory rises while request rate is flat, and memory never falls.
   - Genuine load: memory rises with request rate, bounded cache entries plateau, and memory falls after load drops.
3. For hanging-request or timeout alerts, call fetch_hanging_requests and fetch_filtered_pod_logs:
   - A non-empty hanging-requests list (endpoint and age) is direct evidence. Report that endpoint as hanging.
   - A log line like "Request exceeded hang threshold" is also direct evidence, even if the list is now empty, because the request may have already ended or been aborted.
   - An empty list with no such log line means no hang was observed. It does not mean the cause is unknown.
4. "NO DATA" means unknown, not zero. If request rate is NO DATA, state that load is unknown and lower confidence.
5. Every claim must cite a specific tool result. No evidence, no claim.
6. If a tool fails or times out, note it and continue; never assume its value.
7. When evidence suffices, stop calling tools and reply in plain text. Use "inconclusive" only when no tool returned direct evidence; if you have any, give the root cause and lower the confidence instead.
Error: <alert + symptom>
Root Cause: <explanation, each claim tied to a tool result>
Confidence: <high|medium|low|inconclusive>
Evidence Gaps: <unknowns, if not high>""")


def investigator_node(state: AgentState):
    messages = list(state.get("messages", []))
    context_gathered = state.get("context_gathered", "")
    iterations = state.get("investigation_iterations", 0)

    # 1. First run
    if not messages:
        alert_data = state.get("alert_payload", {})
        human_msg = HumanMessage(content=(
            f"Prometheus alert:\n{_compact(alert_data)}\nInvestigate it with your tools."
        ))
        response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG, human_msg])
        return {
            "messages": [human_msg, response],
            "context_gathered": f"Started investigation for alert: {_alert_name(alert_data)}",
            "investigation_iterations": 1,
        }

    # 2. Fold new ToolMessages (since the last AI turn) into context_gathered
    new_findings = []
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            new_findings.append(f"- [{m.name}]: {str(m.content)[:1500]}")
        elif isinstance(m, AIMessage):
            break
    new_findings.reverse()
    if new_findings:
        context_gathered = context_gathered + "\n" + "\n".join(new_findings)

    iterations += 1

    # 3. Hard cap — force a conclusion instead of trusting the prompt alone
    if iterations >= MAX_INVESTIGATION_ITERATIONS:
        force_msg = HumanMessage(content=(
            "Max investigation steps reached. No more tools: give your final findings now, "
            "with low/inconclusive confidence if the evidence is thin."
        ))
        # llm WITHOUT tools bound, so it physically cannot call another tool
        response = llm.invoke([INVESTIGATOR_SYS_MSG] + messages + [force_msg])
        if response.content:
            context_gathered += f"\n[Final Summary - forced by iteration cap]: {response.content}"
        return {
            "messages": [force_msg, response],
            "context_gathered": context_gathered,
            "investigation_iterations": iterations,
        }

    # 4. Normal loop continuation
    response = llm_with_tools.invoke([INVESTIGATOR_SYS_MSG] + messages)

    if response.content and not response.tool_calls:
        context_gathered += f"\n[Investigation Finding]: {response.content}"

    return {
        "messages": [response],
        "context_gathered": context_gathered,
        "investigation_iterations": iterations,
    }


# 2. Proposer

PROPOSER_SYS_MSG = SystemMessage(content="""You are an SRE remediation expert. Return a RemediationPlan.

Priority: a HUMAN OPERATOR OVERRIDE in the prompt is the main directive. It beats every rule below and your own analysis (latest line wins). Format the requested action exactly, even if the diagnosis is inconclusive. Only limits: action_type must be a valid option, and reasoning must be honest and say the action is operator-directed.

Prefer the lowest-impact action that fixes the root cause:
low: kill_idle_transaction, abort_hanging_requests | medium: restart_deployment, scale_deployment | last resort: delete_pod, increase_memory_limit, increase_cpu_limit.
Never raise resource limits if a blocking process, leaked connection or hoarding query was found; kill it instead.
For a confirmed memory leak (buffers growing, memory never falls), prefer restart_deployment. Raising the limit only delays the next failure. Use increase_memory_limit only for genuine load.

Rules:
- No clear root cause: return empty proposed_actions with confidence "inconclusive". Don't guess.
- Each action's reasoning must cite specific investigation evidence.
- Every action that needs a value must include it, taken from the evidence:
  - kill_idle_transaction: value = numeric PID; target_name = the postgres pod.
  - abort_hanging_requests: value = age threshold in seconds (use 10 unless evidence says otherwise); target_name = the backend pod.
  - increase_memory_limit: value = new limit in Mi, e.g. "768Mi". Use about 1.5x the current limit, never more than 2x. target_name = the deployment.
  - increase_cpu_limit: value = new limit in millicores, e.g. "600m". Use about 1.5x the current limit, never more than 2x. target_name = the deployment.
  - scale_deployment: value = target replica count, current + 1 at most; target_name = the deployment.
- If an action needs the current limit or replica count and the evidence doesn't contain it, return empty proposed_actions with confidence "low" and say which value is missing. Never invent the current value.
- After a failed verification, don't repeat the same action_type on the same target; propose an alternative, or return empty with confidence "low".""")


def proposer_node(state: AgentState):
    context = state.get("context_gathered", "")
    alert = state.get("alert_payload", {})
    critic_feedback = state.get("critic_feedback", "")
    human_feedback = state.get("human_feedback", "")
    is_safe = state.get("is_safe", True)
    verification_attempts = state.get("verification_attempts", 0)
    execution_logs = state.get("execution_logs", "")

    structured_llm = llm.with_structured_output(RemediationPlan)

    critic_prompt = ""
    if not is_safe and critic_feedback:
        critic_prompt = f"\n\nPREVIOUS PLAN REJECTED BY CRITIC:\n{critic_feedback}\nPropose a new plan addressing it."

    human_prompt = ""
    if human_feedback:
        human_prompt = f"\n\nHUMAN OPERATOR OVERRIDE (main directive; latest line wins):\n{human_feedback}"

    retry_prompt = ""
    if verification_attempts > 0:
        retry_prompt = (
            f"\n\nPRIOR ATTEMPT {verification_attempts} DID NOT RESOLVE THE ALERT. "
            f"Its logs:\n{execution_logs}\nDon't repeat it."
        )

    prompt = (
        f"Alert:\n{_compact(alert)}\n\nInvestigation:\n{context}"
        f"{critic_prompt}{human_prompt}{retry_prompt}\n\nPropose a remediation plan."
    )

    try:
        plan = structured_llm.invoke([PROPOSER_SYS_MSG, HumanMessage(content=prompt)])
        if plan is None:
            raise ValueError("structured output returned None")
    except Exception as e:
        # Fail safe: an empty plan routes to the abort/report path instead of crashing the graph
        print(f"   └─ ⚠️ [PROPOSER ERROR] {e}. Falling back to an empty plan.")
        plan = _empty_plan(f"Proposer failed to produce a valid structured plan: {e}")

    # Anti-hallucination guard: an inconclusive diagnosis proposes nothing — UNLESS the human
    # explicitly asked for an action; the operator's instruction is the main directive.
    if plan.confidence == "inconclusive" and not human_feedback:
        plan.proposed_actions = []

    if not plan.proposed_actions:
        print(f"   └─ Proposed Plan: [EMPTY - {plan.confidence}]")
    else:
        print(f"   └─ Proposed Plan: {[f'{a.action_type}({a.target_name})' for a in plan.proposed_actions]}")

    return {"proposed_plan": plan}


# ---------------------------------------------------------------------------
# 3. Critic
# ---------------------------------------------------------------------------
CRITIC_SYS_MSG = SystemMessage(content=f"""You are a strict Kubernetes safety critic. Answer with the CriticReview tool.

Reject (is_safe=false, with specific feedback) if any holds:
- an action lacks support in the investigation evidence
- memory/cpu limit increase or scale_deployment despite a found blocking / idle-in-transaction / hoarding query
- kill_idle_transaction value isn't a numeric PID from the evidence
- scale to 0 replicas, or delete_pod on a database pod
- a higher-impact action is used where a lower-impact one would do
Otherwise approve.
{SOP_CONTEXT}""")


def critic_node(state: AgentState):
    """Reviews the proposed plan against safety rules before execution."""
    plan = state.get("proposed_plan")
    attempts = state.get("critic_attempts", 0)

    # Fast path: nothing to review (proposer refused to guess) — skip the LLM call
    raw_actions = _get(plan, "proposed_actions", []) or []
    if not raw_actions:
        print("   └─ [CRITIC] Bypassing safety review. Plan is empty.")
        return {"is_safe": True, "critic_feedback": "No actions proposed."}

    structured_llm = llm.with_structured_output(CriticReview)
    prompt = (
        f"Alert:\n{_compact(state.get('alert_payload', {}))}\n\n"
        f"Investigation:\n{state.get('context_gathered', '')}\n\n"
        f"Plan:\n{_plan_json(plan)}\n\nReview this plan for safety."
    )
    if state.get("human_feedback"):
        prompt += (
            f"\n\nNOTE: this plan follows a HUMAN OPERATOR OVERRIDE:\n{state['human_feedback']}\n"
            "That instruction is the main directive: reject only if clearly destructive or malformed. "
            "Your feedback is advisory; the operator decides."
        )

    try:
        review: CriticReview = structured_llm.invoke([CRITIC_SYS_MSG, HumanMessage(content=prompt)])
        status = "✅ APPROVED" if review.is_safe else "❌ REJECTED"
        print(f"   └─ Critic Decision: {status}")
        if not review.is_safe:
            print(f"      └─ Reason: {review.feedback}")
        out = {"is_safe": review.is_safe, "critic_feedback": review.feedback}
        if not review.is_safe:
            out["critic_attempts"] = attempts + 1
        return out
    except Exception as e:
        # Fail safe: reject, but the human still sees the warning at the HITL step
        print(f"   └─ ⚠️ [CRITIC ERROR] LLM failed to parse. Rejecting for safety. {e}")
        return {
            "is_safe": False,
            "critic_feedback": "Critic LLM crashed or failed to output structured JSON.",
            "critic_attempts": attempts + 1,
        }


def critic_router(state: AgentState):
    """Bounce rejected plans back to the proposer a limited number of times;
    after that (or under a human override) the human decides at the HITL step."""
    if state.get("is_safe", True):
        return "executor_node"
    if state.get("human_feedback"):
        return "executor_node"
    if state.get("critic_attempts", 0) >= MAX_CRITIC_ATTEMPTS:
        return "executor_node"
    return "proposer_node"


# ---------------------------------------------------------------------------
# 4. Executor (HITL + K8s SDK)
# ---------------------------------------------------------------------------
def _patch_container_limit(apps_v1_api, name: str, namespace: str, resource: str, value: str) -> str:
    """Patches the first container of a Deployment with a new resource limit.
    Returns the container name that was patched."""
    deployment = apps_v1_api.read_namespaced_deployment(name=name, namespace=namespace)
    container_name = deployment.spec.template.spec.containers[0].name
    body = {"spec": {"template": {"spec": {"containers": [
        {"name": container_name, "resources": {"limits": {resource: value}}}
    ]}}}}
    apps_v1_api.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
    return container_name


def _run_action(action, v1_api, apps_v1_api) -> str:
    """Executes ONE action. Returns a log line; raises on failure."""
    action_type = _get(action, "action_type")
    target = _get(action, "target_name")
    ns = _get(action, "namespace", "default") or "default"
    val = _get(action, "value")

    if action_type == "delete_pod":
        v1_api.delete_namespaced_pod(name=target, namespace=ns)
        return f"[SUCCESS] Deleted pod '{target}' in '{ns}'"

    if action_type == "restart_deployment":
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
        apps_v1_api.patch_namespaced_deployment(name=target, namespace=ns, body=body)
        return f"[SUCCESS] Restarted deployment '{target}' in '{ns}'"

    if action_type == "scale_deployment":
        replicas = int(val)  # raises on garbage
        if not 1 <= replicas <= MAX_SCALE_REPLICAS:
            raise ValueError(f"Refusing to scale to {replicas} replicas (allowed: 1..{MAX_SCALE_REPLICAS})")
        apps_v1_api.patch_namespaced_deployment_scale(name=target, namespace=ns, body={"spec": {"replicas": replicas}})
        return f"[SUCCESS] Scaled deployment '{target}' to {replicas}"

    if action_type == "increase_memory_limit":
        cname = _patch_container_limit(apps_v1_api, target, ns, "memory", val)
        return f"[SUCCESS] Increased memory limit for '{target}' (container: {cname}) to {val}"

    if action_type == "increase_cpu_limit":
        cname = _patch_container_limit(apps_v1_api, target, ns, "cpu", val)
        return f"[SUCCESS] Increased CPU limit for '{target}' (container: {cname}) to {val}"

    if action_type == "kill_idle_transaction":
        pid = int(val)  # fail loud on a non-numeric value instead of exec'ing garbage SQL
        # Locates the postgres pod itself, so a wrong target_name from the LLM can't misdirect it
        resp = run_psql(f"SELECT pg_terminate_backend({pid});", ns).strip().lower()
        if resp != "t":
            raise RuntimeError(f"pg_terminate_backend({pid}) did not return true (got: '{resp}'); PID may be gone")
        return f"[SUCCESS] Terminated backend PID {pid}"

    if action_type == "abort_hanging_requests":
        threshold = int(val or 10)
        exec_cmd = ["curl", "-s", "-X", "POST", f"http://127.0.0.1:3000/admin/requests/abort?threshold={threshold}"]
        resp = stream(
            v1_api.connect_get_namespaced_pod_exec,
            name=target, namespace=ns, command=exec_cmd,
            stderr=True, stdin=False, stdout=True, tty=False,
        )
        # stream() may return a Python-style dict string (single quotes), so match loosely
        if "success" not in str(resp):
            raise RuntimeError(f"Abort did not confirm success: {str(resp)[:200]}")
        return f"[SUCCESS] Aborted requests exceeding {threshold}s on '{target}': {resp}"

def executor_node(state: AgentState):
    """Executes actions via K8s SDK behind a single human-in-the-loop approval."""
    plan = state.get("proposed_plan")
    is_safe = state.get("is_safe", True)
    critic_feedback = state.get("critic_feedback", "")

    raw_actions = _get(plan, "proposed_actions", []) or []
    if not raw_actions:
        print("\n⚠️ AI proposed no safe actions. Moving to post-mortem.")
        return {
            "execution_logs": "[NO ACTION] AI determined no safe action could be taken.",
            "hitl_route": "abort",
        }

    # Format the ENTIRE plan for a single review
    action_summaries = []
    for action in raw_actions:
        summary = (
            f"- {_get(action, 'action_type')} on {_get(action, 'target_name', 'Unknown')} "
            f"(namespace: {_get(action, 'namespace', 'default')})"
        )
        if _get(action, "value"):
            summary += f" with value: {_get(action, 'value')}"
        action_summaries.append(summary)
    summary_text = "\n".join(action_summaries)

    warning_banner = f"\n⚠️ CRITIC SAFETY WARNING: {critic_feedback}\n" if not is_safe else ""

    # ONE interrupt for the whole plan. NOTE: on resume this node re-runs from the top,
    # so nothing with side effects may happen before this call.
    user_response = interrupt({
        "is_safe": is_safe,
        "warning": warning_banner,
        "question": (
            f"{warning_banner}Proposed Plan to Execute:\n{summary_text}\n\n"
            "How do you want to proceed? (approve / abort / feedback)"
        ),
    })
    print(f"\n[DEBUG] user_response received from FastAPI: {user_response}")

    # Parse the decision. Anything unrecognised is treated as ABORT (fail safe).
    if isinstance(user_response, dict):
        decision = str(user_response.get("decision", "abort")).strip().lower()
        human_feedback = str(user_response.get("feedback", "") or "")
    else:
        decision = str(user_response).strip().lower()
        human_feedback = ""
    if decision in APPROVE_WORDS:
        decision = "approve"

    if decision == "feedback":
        print(f"\n🔄 Routing back to Proposer with human feedback: {human_feedback}")
        new_fb = human_feedback or "Operator rejected the plan; propose a different approach."
        prior_fb = state.get("human_feedback", "")
        return {
            # keep every round, oldest first — the proposer treats the LAST line as the winner
            "human_feedback": f"{prior_fb}\n{new_fb}".strip() if prior_fb else new_fb,
            "critic_attempts": 0,
            "is_safe": True,        # reset stale critic verdict; the new plan gets a fresh review
            "critic_feedback": "",
            "hitl_route": "replan",
        }

    if decision != "approve":
        print("\n❌ Execution aborted by human. Generating incident report...")
        return {
            "execution_logs": "[ABORTED BY HUMAN OPERATOR] No actions executed.",
            "hitl_route": "abort",
        }

    # ---- Approved: execute ----
    print("\n🚀 [EXECUTING K8S SDK]...")
    output_logs = ""
    v1_api, apps_v1_api = get_gke_client()

    for action in raw_actions:
        target = _get(action, "target_name")
        try:
            line = _run_action(action, v1_api, apps_v1_api)
            output_logs += f"\n{line}"
            print(f"   └─ ✅ {line}")
        except Exception as e:
            output_logs += f"\n[ERROR] Execution failed for {target}: {e}"
            print(f"   └─ ❌ Execution error: {e}")
            break  # don't continue a multi-step plan after a failure

    return {"execution_logs": output_logs, "hitl_route": "execute"}


def hitl_router(state: AgentState):
    if state.get("hitl_route") == "replan":
        return "proposer_node"
    return "reporter_node"


# ---------------------------------------------------------------------------
# 5. Reporter
# ---------------------------------------------------------------------------
def _parse_ts(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    return f"{m}m {s}s" if m else f"{s}s"


OUTCOMES = {
    "execute": "Remediation executed",
    "abort": "Not remediated (operator aborted, or no safe action was proposed)",
    "replan": "Plan revised by operator feedback",
}


def reporter_node(state: AgentState):
    """Generates a structured incident report for later reference."""
    print("\n[REPORTER] Generating incident report...")

    alert = state.get("alert_payload", {})
    context = state.get("context_gathered", "")
    plan = state.get("proposed_plan")
    logs = state.get("execution_logs", "")
    hitl_route = state.get("hitl_route", "execute")
    critic_feedback = state.get("critic_feedback", "") or "None"
    human_feedback = state.get("human_feedback", "") or "None"

    now = datetime.now(timezone.utc)
    end_time_str = now.isoformat()
    start_str = state.get("incident_start_time", end_time_str)
    try:
        duration_seconds = int((now - _parse_ts(start_str)).total_seconds())
    except Exception:
        duration_seconds = 0

    alert_name = _alert_name(alert)
    labels = alert.get("labels", {})
    target = labels.get("deployment") or labels.get("pod", "UnknownResource")

    # Header is built in code so the facts are exact and never rewritten by the LLM
    header = f"""# Incident Report: {alert_name}

| Field | Value |
|---|---|
| Alert | {alert_name} |
| Target | {target} |
| Severity label | {labels.get("severity", "unknown")} |
| Incident start (UTC) | {start_str} |
| Report generated (UTC) | {end_time_str} |
| Duration (alert to report) | {_fmt_duration(duration_seconds)} |
| Outcome | {OUTCOMES.get(hitl_route, hitl_route)} |

---
"""

    prompt = f"""Write the body of a formal SRE incident report in Markdown, for future reference by engineers who were not present.

Rules:
- Use only facts present in the inputs below. If something is not in the inputs, write "Not available". Never infer or invent values.
- Plain professional tone. No emojis, no decorative symbols, no title (a header table is added separately).
- Quote exact technical details: metric values, PIDs, endpoint names, error lines.
- Past tense for what happened, imperative for follow-up actions.

Inputs
Alert: {alert_name} | Target: {target} | Route: {hitl_route} (execute = fix applied, abort = operator stopped it or no safe action, replan = operator changed the plan)
Investigation findings: {context}
Proposed plan: {_plan_json(plan)}
Critic feedback: {critic_feedback}
Operator feedback: {human_feedback}
Execution log: {logs}

Use exactly these sections, in this order:
## Summary
(2-3 sentences: what failed, what was found, final state.)
## Root Cause
(The diagnosis and confidence level, each claim tied to a specific finding.)
## Supporting Evidence
(Bulleted list of the concrete observations from the investigation.)
## Proposed Remediation
(Each proposed action with target, value and the reasoning given.)
## Operator Review
(Approved without modification / aborted / feedback given, quoting the feedback.)
## Execution Result
(Each action and its result. If nothing ran, write "No actions were executed.")
## Follow-up Actions
(If unresolved or aborted: the specific steps on-call should take, with exact error lines, PIDs or commands. If resolved: preventive items such as alert tuning or a code fix for the underlying defect.)"""

    report_response = llm.invoke([
        SystemMessage(content="You are a senior SRE writing precise, neutral incident reports. You never invent facts."),
        HumanMessage(content=prompt),
    ])
    post_mortem_markdown = header + "\n" + report_response.content

    # A GitHub failure must not crash the graph after the fix already ran
    try:
        commit_url = commit_post_mortem_to_repo(
            post_mortem_content=post_mortem_markdown,
            alert_name=alert_name,
            target_resource=target,
        )
    except Exception as e:
        print(f"   └─ [REPORTER] Failed to commit post-mortem: {e}")
        commit_url = ""

    return {
        "incident_end_time": end_time_str,
        "post_mortem_md": post_mortem_markdown,
        "github_commit_url": commit_url,
    }


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
workflow = StateGraph(AgentState)

workflow.add_node("investigator_node", investigator_node)
workflow.add_node("tools", tool_node)
workflow.add_node("proposer_node", proposer_node)
workflow.add_node("critic_node", critic_node)
workflow.add_node("executor_node", executor_node)
workflow.add_node("reporter_node", reporter_node)

workflow.set_entry_point("investigator_node")

# Investigator <-> tools loop; when no tool call is made, move to the proposer
workflow.add_conditional_edges("investigator_node", tools_condition, {"tools": "tools", END: "proposer_node"})
workflow.add_edge("tools", "investigator_node")

# Proposer -> Critic -> (back to Proposer if rejected & attempts left) | Executor
workflow.add_edge("proposer_node", "critic_node")
workflow.add_conditional_edges(
    "critic_node",
    critic_router,
    {"proposer_node": "proposer_node", "executor_node": "executor_node"},
)

# Executor -> Reporter, or back to the Proposer when the human gave feedback
workflow.add_conditional_edges(
    "executor_node",
    hitl_router,
    {"proposer_node": "proposer_node", "reporter_node": "reporter_node"},
)

workflow.add_edge("reporter_node", END)

# A checkpointer is REQUIRED so the graph can pause/resume for HITL
memory = MemorySaver()
agent_app = workflow.compile(checkpointer=memory)