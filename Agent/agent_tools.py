from typing import Optional
from langchain_core.tools import tool
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
import requests
import json
import re
import os
import time


BACKEND_ADMIN_URL = os.environ.get("BACKEND_ADMIN_URL", "http://backend-service:3000")
PROM_URL = os.environ.get("PROM_URL", "http://prometheus-service:9090")

# k8s resource names / pod names: letters, digits, '-' and '.'. Used to stop the LLM
# from smuggling PromQL into f-string queries.
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]{0,252}$")


def get_gke_client():
    try:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        return v1, apps_v1
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Kubernetes client: {e}")


def _k8s_error(e: ApiException, what: str) -> str:
    """Turns a Kubernetes API error into text the LLM cannot misread.
    A 401/403 is a PERMISSION problem of this agent, not evidence about the resource."""
    if e.status in (401, 403):
        return (
            f"ACCESS DENIED ({e.status}) while {what}: the agent's service account lacks RBAC "
            "permission for this call. This says NOTHING about whether the resource exists or is "
            "healthy; treat it as an evidence gap."
        )
    if e.status == 404:
        return f"Not found (404) while {what}."
    return f"Kubernetes API Error while {what}: {e.reason}"


# ---------------------------------------------------------------------------
# Postgres helpers (shared by the diagnostic tools AND the executor)
# ---------------------------------------------------------------------------
# HOW THE DB PASSWORD IS FOUND (first match wins):
#   A. PG_PASSWORD on the AGENT (e.g. from your Key Vault secret) -> sent to psql over STDIN,
#      so it never appears in the exec command line, API-server audit logs or `ps`.
#   B. Inside the POSTGRES container, first match wins: file PG_PASSWORD_FILE, variable named by
#      PG_PASSWORD_ENV_VAR, the Key Vault mount /mnt/secrets/app-env (DEFAULT: your StatefulSet's own
#      start script reads the password from it; a `kubectl exec` shell does NOT inherit the
#      POSTGRESQL_PASSWORD that script exports), the container's POSTGRES_PASSWORD_FILE, or
#      PGPASSWORD / POSTGRES_PASSWORD / POSTGRESQL_POSTGRES_PASSWORD / POSTGRESQL_PASSWORD.
#      Files may hold a `DB_PASSWORD=...` line or just the raw password (same parsing as your YAML).
# PG_PASSWORD_ENV_VAR / PG_PASSWORD_FILE / PG_USER are just NAMES/PATHS, not secrets.
# The query is passed as "$1", never spliced into the shell string. psql runs with -w, so it
# fails fast instead of waiting for a password prompt that nobody can answer.
# sh positional args: $1=query $2=env-var name $3=password file $4=db user $5="1" if password is on stdin $6=default secret file
_DEFAULT_SECRET_FILE = "/mnt/secrets/app-env"  # Key Vault CSI mount of the postgres StatefulSet
_PSQL_SCRIPT = r'''
read_pw_file() {
  if grep -q '^DB_PASSWORD=' "$1"; then
    grep '^DB_PASSWORD=' "$1" | head -1 | cut -d= -f2- | tr -d '"\r\n'
  else
    tr -d '\r\n' < "$1"
  fi
}
if [ "$5" = "1" ]; then
  IFS= read -r PGPASSWORD
elif [ -n "$3" ] && [ -r "$3" ]; then
  PGPASSWORD="$(read_pw_file "$3")"
elif [ -n "$2" ]; then
  eval "PGPASSWORD=\"\${$2}\""
elif [ -r "$6" ]; then
  PGPASSWORD="$(read_pw_file "$6")"
elif [ -n "$POSTGRES_PASSWORD_FILE" ] && [ -r "$POSTGRES_PASSWORD_FILE" ]; then
  PGPASSWORD="$(read_pw_file "$POSTGRES_PASSWORD_FILE")"
else
  PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-${POSTGRESQL_POSTGRES_PASSWORD:-$POSTGRESQL_PASSWORD}}}"
fi
export PGPASSWORD
exec psql -w -U "${4:-${POSTGRES_USER:-postgres}}" -d postgres -t -A -F " | " -c "$1"
'''

# Prints variable NAMES (never values) and likely secret mount paths, to help find the password.
_HINT_SCRIPT = (
    'echo "env var names: $(env | cut -d= -f1 | grep -i -E "pass|pwd|pg|postgres|secret" | sort | tr "\\n" " ")"; '
    'echo "mounts: $(ls -d /mnt/*/* /run/secrets/* /etc/postgresql* 2>/dev/null | tr "\\n" " ")"'
)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_OK = re.compile(r"^[\w./\-]+$")
_EXEC_TIMEOUT_SEC = 30


def _psql_cmd(query: str, stdin_pw: bool = False) -> list:
    """Builds the exec command. Names/paths come from the agent's own env and are validated,
    because the env-var name is expanded by the shell (eval)."""
    var = os.environ.get("PG_PASSWORD_ENV_VAR", "").strip()
    path = os.environ.get("PG_PASSWORD_FILE", "").strip()
    user = os.environ.get("PG_USER", "").strip()
    if var and not _ENV_NAME.match(var):
        raise RuntimeError("PG_PASSWORD_ENV_VAR is not a valid variable name")
    if path and not _PATH_OK.match(path):
        raise RuntimeError("PG_PASSWORD_FILE contains unexpected characters")
    if user and not _PATH_OK.match(user):
        raise RuntimeError("PG_USER contains unexpected characters")
    return ["sh", "-c", _PSQL_SCRIPT, "sh", query, var, path, user, "1" if stdin_pw else "", _DEFAULT_SECRET_FILE]


def _exec_in_pod(pod: str, namespace: str, command: list, stdin_text: Optional[str] = None) -> str:
    """Runs a command in a pod and returns stdout. Raises RuntimeError (with stderr) on a non-zero
    exit, so a failed psql is an error instead of 'successful' output text."""
    v1_api, _ = get_gke_client()
    ws = stream(
        v1_api.connect_get_namespaced_pod_exec,
        name=pod, namespace=namespace, command=command,
        stderr=True, stdin=stdin_text is not None, stdout=True, tty=False,
        _preload_content=False,
    )
    out, err = [], []
    try:
        if stdin_text is not None:
            ws.write_stdin(stdin_text + "\n")
        deadline = time.time() + _EXEC_TIMEOUT_SEC
        while ws.is_open() and time.time() < deadline:
            ws.update(timeout=1)
            if ws.peek_stdout():
                out.append(ws.read_stdout())
            if ws.peek_stderr():
                err.append(ws.read_stderr())
        timed_out = ws.is_open()
        rc = getattr(ws, "returncode", None)
    finally:
        ws.close()
    stdout, stderr = "".join(out), "".join(err).strip()
    if timed_out:
        raise RuntimeError(f"command timed out after {_EXEC_TIMEOUT_SEC}s in pod '{pod}'")
    if rc not in (0, None) or (rc is None and stderr and not stdout.strip()):
        raise RuntimeError(stderr or f"exit code {rc}")
    return stdout


def _exec_psql(pod: str, namespace: str, query: str) -> str:
    pw = os.environ.get("PG_PASSWORD", "")
    try:
        return _exec_in_pod(pod, namespace, _psql_cmd(query, stdin_pw=bool(pw)), stdin_text=pw if pw else None)
    except RuntimeError as e:
        msg = str(e)
        if "no password supplied" in msg or "authentication failed" in msg:
            try:
                hint = _exec_in_pod(pod, namespace, ["sh", "-c", _HINT_SCRIPT]).strip().replace("\n", "; ")
            except Exception:
                hint = "(could not inspect the container)"
            raise RuntimeError(
                f"DB AUTH FAILED on pod '{pod}': the agent has no valid database password"
                f"{' (PG_PASSWORD is set but was rejected)' if pw else ''}. "
                f"Container hints: {hint}. This is a configuration gap of the agent and says "
                "NOTHING about the database state; treat it as an evidence gap."
            )
        raise RuntimeError(f"psql failed in pod '{pod}': {msg}")


_PRIMARY_CACHE: dict = {}


def find_postgres_pod(namespace: str = "default") -> Optional[str]:
    """Returns the PRIMARY postgres pod. With several postgres pods (primary + replicas) the first
    one found may be a replica: idle-in-transaction sessions and pg_terminate_backend only work on the
    primary, so each candidate is probed with pg_is_in_recovery()."""
    cached = _PRIMARY_CACHE.get(namespace)
    if cached and time.time() - cached[1] < 60:
        return cached[0]

    v1_api, _ = get_gke_client()
    names = [p.metadata.name for p in v1_api.list_namespaced_pod(namespace=namespace, label_selector="app=postgres").items]
    if not names:
        names = [p.metadata.name for p in v1_api.list_namespaced_pod(namespace=namespace).items if "postgres" in p.metadata.name]
    names = sorted(n for n in names if "exporter" not in n)
    if not names:
        return None
    if len(names) == 1:
        return names[0]

    for n in names:
        try:
            if _exec_psql(n, namespace, "SELECT pg_is_in_recovery();").strip().lower() == "f":
                _PRIMARY_CACHE[namespace] = (n, time.time())
                return n
        except Exception:
            continue
    return names[0]  # could not probe (e.g. auth failure): the real call will report why


def run_psql(query: str, namespace: str = "default") -> str:
    """Runs a query inside the primary postgres pod. Raises RuntimeError on failure.
    Only ever called with fixed queries (or a pre-validated integer PID), never
    with free-form LLM text."""
    pod = find_postgres_pod(namespace)
    if not pod:
        raise RuntimeError("Could not locate a PostgreSQL pod in this namespace.")
    # psql flags: -t tuples only, -A unaligned, -F' | ' separator that survives commas in query text
    return _exec_psql(pod, namespace, query)


# ---------------------------------------------------------------------------
# Investigation tools (read-only)
# ---------------------------------------------------------------------------
@tool
def fetch_filtered_pod_logs(namespace: str, pod_name: str, tail_lines: int = 100) -> str:
    """
    Fetches the most recent logs from a specific pod and filters for errors.
    Use this tool when you need to see why a specific pod is crashing or failing.

    Args:
        namespace: The Kubernetes namespace (e.g., 'default').
        pod_name: The exact name of the pod.
        tail_lines: How many recent lines to fetch before filtering.
    """
    try:
        v1_api, _ = get_gke_client()
        logs = v1_api.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)

        error_patterns = re.compile(r"(error|fatal|exception|fail|timeout|oomkilled|denied)", re.IGNORECASE)
        filtered_logs = [line for line in logs.split("\n") if error_patterns.search(line)]

        if not filtered_logs:
            return (
                f"Logs fetched successfully. No explicit error keywords found in the last "
                f"{tail_lines} lines. The pod might be stuck rather than crashing."
            )
        return "Filtered Error Logs:\n" + "\n".join(filtered_logs)

    except ApiException as e:
        return _k8s_error(e, f"fetching logs for pod {pod_name}")
    except Exception as e:
        return f"Error: could not fetch logs for pod {pod_name}: {e}"


@tool
def get_pod_status(namespace: str, pod_name: str) -> str:
    """
    Retrieves the current phase, restart count, and state reasons for a specific pod.
    Use this tool to check if a pod is in CrashLoopBackOff or Pending state.
    """
    try:
        v1_api, _ = get_gke_client()
        pod = v1_api.read_namespaced_pod(name=pod_name, namespace=namespace)

        status_info = {
            "phase": pod.status.phase,
            "pod_ip": pod.status.pod_ip,
            "node_name": pod.spec.node_name,
            "container_statuses": [],
        }

        for container in pod.status.container_statuses or []:
            if container.state and container.state.waiting:
                state_dict = {
                    "state": "Waiting",
                    "reason": container.state.waiting.reason,
                    "message": container.state.waiting.message,
                }
            elif container.state and container.state.terminated:
                state_dict = {
                    "state": "Terminated",
                    "reason": container.state.terminated.reason,
                    "exit_code": container.state.terminated.exit_code,
                }
            else:
                state_dict = {"state": "Running"}

            status_info["container_statuses"].append({
                "name": container.name,
                "ready": container.ready,
                "restart_count": container.restart_count,
                "current_state": state_dict,
            })

        return json.dumps(status_info, indent=2)
    except ApiException as e:
        return _k8s_error(e, f"reading status of pod {pod_name}")
    except Exception as e:
        return f"Error: could not read pod status for {pod_name}: {e}"


@tool
def get_deployment_status(namespace: str, deployment_name: str) -> str:
    """Returns a deployment's desired/ready/available replicas, rollout conditions and pod selector.
    Use for alerts about missing, scaled-to-zero or unavailable deployments."""
    try:
        _, apps_v1_api = get_gke_client()
        d = apps_v1_api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        s = d.status
        return json.dumps({
            "desired_replicas": d.spec.replicas,
            "ready": s.ready_replicas or 0,
            "available": s.available_replicas or 0,
            "updated": s.updated_replicas or 0,
            "unavailable": s.unavailable_replicas or 0,
            "selector": d.spec.selector.match_labels,
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                for c in (s.conditions or [])
            ],
        }, indent=2)
    except ApiException as e:
        return _k8s_error(e, f"reading deployment {deployment_name}")
    except Exception as e:
        return f"Error: could not read deployment {deployment_name}: {e}"


@tool
def list_pods(namespace: str, label_selector: str = "") -> str:
    """Lists pods (name, phase, ready, restarts) in a namespace, optionally filtered by a label
    selector such as 'app=backend'. Use it to find real pod names before asking for logs/status."""
    try:
        v1_api, _ = get_gke_client()
        pods = v1_api.list_namespaced_pod(namespace=namespace, label_selector=label_selector or None)
        if not pods.items:
            return f"No pods found in namespace '{namespace}' with selector '{label_selector}'."
        lines = []
        for p in pods.items[:50]:
            cs = p.status.container_statuses or []
            ready = sum(1 for c in cs if c.ready)
            restarts = sum(c.restart_count for c in cs)
            lines.append(f"{p.metadata.name} | {p.status.phase} | ready {ready}/{len(cs)} | restarts {restarts}")
        return "name | phase | ready | restarts\n" + "\n".join(lines)
    except ApiException as e:
        return _k8s_error(e, f"listing pods in {namespace}")
    except Exception as e:
        return f"Error: could not list pods in {namespace}: {e}"


@tool
def fetch_hanging_requests(threshold_sec: int = 30) -> str:
    """Lists in-flight HTTP requests older than threshold_sec WITHOUT aborting
    them — for deciding whether an abort is warranted, not performing it."""
    try:
        resp = requests.get(
            f"{BACKEND_ADMIN_URL}/admin/requests/list",
            params={"threshold": threshold_sec},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return f"Failed to fetch hanging requests: {str(e)}"


@tool
def get_idle_in_transaction(namespace: str = "default", idle_threshold_sec: int = 30) -> str:
    """Lists connections idle-in-transaction longer than idle_threshold_sec — these
    hold row locks and pool slots without doing work. This is exactly the bug pattern
    in /api/orders/checkout (missing ROLLBACK on early return)."""
    query = f"""
    SELECT pid, usename, application_name, now() - state_change AS idle_duration, query
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
      AND now() - state_change > interval '{int(idle_threshold_sec)} seconds'
    ORDER BY idle_duration DESC;
    """
    try:
        result = run_psql(query, namespace)
    except Exception as e:
        return f"Error executing diagnostic query: {e}"
    if not result.strip():
        return "No idle-in-transaction connections found over the threshold."
    return "pid | usename | application_name | idle_duration | query\n" + result


@tool
def get_blocking_transactions(namespace: str = "default") -> str:
    """Finds transactions currently blocking other transactions, with the
    blocking PID, blocked PID, and how long the blocker has held its lock.
    This is the authoritative check for the lock-contention scenario — if this
    returns a row, that PID is a confirmed cause, not just a correlation. Use
    this before proposing any termination action."""
    query = """
    SELECT
        blocked.pid AS blocked_pid,
        blocked.query AS blocked_query,
        blocking.pid AS blocking_pid,
        blocking.query AS blocking_query,
        now() - blocking.state_change AS blocking_duration
    FROM pg_stat_activity blocked
    CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS b(pid)
    JOIN pg_stat_activity blocking ON blocking.pid = b.pid;
    """
    try:
        result = run_psql(query, namespace)
    except Exception as e:
        return f"Error executing diagnostic query: {e}"
    # With -t (tuples only) psql prints no "(0 rows)" footer, so empty output == no rows.
    if not result.strip():
        return "No blocking transactions found — no lock contention detected at this time."
    return "blocked_pid | blocked_query | blocking_pid | blocking_query | blocking_duration\n" + result


def _query_prometheus_range(promql: str, window_minutes: int, step: str = "30s") -> str:
    """Internal helper — runs a PromQL range query over the trailing window_minutes.
    Not a tool itself; called by get_memory_trend."""
    try:
        end = time.time()
        start = end - (window_minutes * 60)
        resp = requests.get(
            f"{PROM_URL}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        if not results:
            return f"No data for range query: {promql}"

        lines = []
        for series in results:
            metric_label = series.get("metric", {})
            values = series.get("values", [])
            if not values:
                continue
            nums = [float(v[1]) for v in values]
            lines.append(
                f"{metric_label}: first={values[0][1]} last={values[-1][1]} "
                f"min={min(nums)} max={max(nums)} (n={len(nums)} points over {window_minutes}m)"
            )
        return "\n".join(lines) if lines else f"No data points for range query: {promql}"
    except Exception as e:
        return f"Prometheus range query failed: {str(e)}"


@tool
def get_memory_trend(pod_name: str, namespace: str = "default", window_minutes: int = 30) -> str:
    """Returns RAM usage for a pod over the trailing window, alongside request
    rate for the same window, so growth can be judged against load rather than
    in isolation. Use this to distinguish genuine memory need (grows with load,
    plateaus) from a leak (grows regardless of load, never drops)."""
    if not _SAFE_NAME.match(pod_name or ""):
        return f"Error: invalid pod_name '{pod_name}'."

    mem_query = f'container_memory_working_set_bytes{{pod="{pod_name}"}}'
    load_query = f'sum(rate(http_requests_total{{pod="{pod_name}"}}[5m]))'
    mem_result = _query_prometheus_range(mem_query, window_minutes)
    load_result = _query_prometheus_range(load_query, window_minutes)
    return f"Memory trend:\n{mem_result}\n\nRequest rate over same window:\n{load_result}"


# Everything the investigator is allowed to call. All read-only.
INVESTIGATION_TOOLS = [
    fetch_filtered_pod_logs,
    get_pod_status,
    get_deployment_status,
    list_pods,
    get_blocking_transactions,
    get_idle_in_transaction,
    fetch_hanging_requests,
    get_memory_trend,
]