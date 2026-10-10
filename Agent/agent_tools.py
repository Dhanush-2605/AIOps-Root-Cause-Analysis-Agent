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


# ---------------------------------------------------------------------------
# Postgres helpers (shared by the diagnostic tools AND the executor)
# ---------------------------------------------------------------------------
def find_postgres_pod(namespace: str = "default") -> Optional[str]:
    v1_api, _ = get_gke_client()
    pods = v1_api.list_namespaced_pod(namespace=namespace, label_selector="app=postgres")
    if pods.items:
        return pods.items[0].metadata.name
    for p in v1_api.list_namespaced_pod(namespace=namespace).items:
        if "postgres" in p.metadata.name:
            return p.metadata.name
    return None


# The password is read from the postgres container's OWN environment (set there by the
# image / Helm chart), so no secret ever lives in this agent's code, logs or checkpoints.
# The query is passed as "$1", never spliced into the shell string.
# Env var names differ per image: official=POSTGRES_PASSWORD, Bitnami=POSTGRESQL_*.
# If your image uses another name, add it to the fallback chain below.
_PSQL_SCRIPT = (
    'export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-${POSTGRESQL_POSTGRES_PASSWORD:-$POSTGRESQL_PASSWORD}}}"; '
    'exec psql -U "${POSTGRES_USER:-postgres}" -d postgres -t -A -F " | " -c "$1"'
)


def run_psql(query: str, namespace: str = "default") -> str:
    """Runs a query inside the postgres pod. Raises RuntimeError on failure.
    Only ever called with fixed queries (or a pre-validated integer PID), never
    with free-form LLM text."""
    pod = find_postgres_pod(namespace)
    if not pod:
        raise RuntimeError("Could not locate a PostgreSQL pod in this namespace.")

    v1_api, _ = get_gke_client()
    # sh -c SCRIPT sh QUERY  ->  $0="sh", $1=QUERY
    # psql flags: -t tuples only, -A unaligned, -F' | ' separator that survives commas in query text
    exec_cmd = ["sh", "-c", _PSQL_SCRIPT, "sh", query]
    try:
        return stream(
            v1_api.connect_get_namespaced_pod_exec,
            name=pod, namespace=namespace, command=exec_cmd,
            stderr=True, stdin=False, stdout=True, tty=False,
        )
    except Exception as e:
        raise RuntimeError(f"psql exec failed in pod '{pod}': {e}")


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
        return f"Kubernetes API Error: Could not fetch logs for pod {pod_name}. Details: {e.reason}"
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
        return f"Kubernetes API Error: {e.reason}"
    except Exception as e:
        return f"Error: could not read pod status for {pod_name}: {e}"


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
    get_blocking_transactions,
    get_idle_in_transaction,
    fetch_hanging_requests,
    get_memory_trend,
]