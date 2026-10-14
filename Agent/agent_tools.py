from contextlib import closing
from typing import Optional
from langchain_core.tools import tool
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from dotenv import dotenv_values
import psycopg2
import requests
import json
import re
import os
import time


BACKEND_ADMIN_URL = os.environ.get("BACKEND_ADMIN_URL", "http://backend-service:3000")
PROM_URL = os.environ.get(
    "PROM_URL",
    "http://prometheus-stack-kube-prom-prometheus.monitoring.svc.cluster.local:9090",
)
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
# Postgres helpers: direct connection through pgpool (same as the Node backend)
# ---------------------------------------------------------------------------
# The Key Vault CSI mount holds a .env-style file (DB_PASSWORD=...). It is parsed with
# python-dotenv into a dict; nothing is exported into os.environ.
# The file is re-read on every connection, so secret rotation is picked up automatically.
SECRET_PATH = os.environ.get("DB_SECRET_PATH", "/mnt/secrets/app-env")

DB_HOST = os.environ.get("DB_HOST", "pgpool-service")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "postgres")  # better: a dedicated role with pg_monitor
DB_NAME = os.environ.get("DB_NAME", "mytierdb")

# pgpool load-balances plain SELECTs across nodes. pg_stat_activity and pg_blocking_pids only
# show the node you land on, and the sessions we care about live on the PRIMARY. This comment
# makes pgpool send the query to the primary.
_PRIMARY_HINT = "/*NO LOAD BALANCE*/ "


def _read_db_password() -> str:
    # Fallback to a plain env var for local dev, when the secret isn't mounted.
    if os.path.exists(SECRET_PATH):
        password = dotenv_values(SECRET_PATH).get("DB_PASSWORD")
        if password:
            return password
        raise RuntimeError(f"DB_PASSWORD not found in {SECRET_PATH}")
    password = os.environ.get("DB_PASSWORD")
    if password:
        return password
    raise RuntimeError(f"Secret file {SECRET_PATH} not found and DB_PASSWORD env var is not set")


def _get_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=_read_db_password(),
        dbname=DB_NAME,
        connect_timeout=5,
        application_name="diagnostic-agent",
        options="-c statement_timeout=10000",  # 10s cap on any diagnostic query
    )
    # Diagnostics must never write, and autocommit avoids leaving our own
    # session 'idle in transaction' (which would pollute the very check we run).
    conn.set_session(readonly=True, autocommit=True)
    return conn


def run_psql(query: str, params: Optional[tuple] = None) -> str:
    """Runs a read-only query on the primary (via pgpool) and returns rows formatted as
    'col | col | col' lines. Raises RuntimeError on failure.
    Values are always passed via `params`, never spliced into the query string."""
    try:
        with closing(_get_conn()) as conn, conn.cursor() as cur:
            cur.execute(_PRIMARY_HINT + query, params)
            rows = cur.fetchall()
    except psycopg2.OperationalError as e:
        msg = str(e).strip()
        if "password authentication failed" in msg or "no password supplied" in msg:
            raise RuntimeError(
                f"DB AUTH FAILED for user '{DB_USER}' at {DB_HOST}:{DB_PORT}: the agent's database "
                "credentials were rejected. This is a configuration gap of the agent and says "
                "NOTHING about the database state; treat it as an evidence gap."
            )
        raise RuntimeError(f"Could not connect/query {DB_HOST}:{DB_PORT}: {msg}")
    except psycopg2.Error as e:
        raise RuntimeError(f"psql query failed: {str(e).strip()}")
    return "\n".join(" | ".join("" if v is None else str(v) for v in row) for row in rows)


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
def get_idle_in_transaction(idle_threshold_sec: int = 30) -> str:
    """Lists connections idle-in-transaction longer than idle_threshold_sec — these
    hold row locks and pool slots without doing work. This is exactly the bug pattern
    in /api/orders/checkout (missing ROLLBACK on early return)."""
    query = """
    SELECT pid, usename, application_name, now() - state_change AS idle_duration, query
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
      AND now() - state_change > make_interval(secs => %s)
    ORDER BY idle_duration DESC;
    """
    try:
        result = run_psql(query, (int(idle_threshold_sec),))
    except Exception as e:
        return f"Error executing diagnostic query: {e}"
    if not result.strip():
        return "No idle-in-transaction connections found over the threshold."
    return "pid | usename | application_name | idle_duration | query\n" + result


@tool
def get_blocking_transactions() -> str:
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
        result = run_psql(query)
    except Exception as e:
        return f"Error executing diagnostic query: {e}"
    if not result.strip():
        return "No blocking transactions found — no lock contention detected at this time."
    return "blocked_pid | blocked_query | blocking_pid | blocking_query | blocking_duration\n" + result

def _query_prometheus_range(promql: str, window_minutes: int, step: str = "30s",
                            in_mib: bool = False) -> str:
    """Runs a PromQL range query and returns a short summary per series."""
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
            return "NO DATA (metric missing or filter matched nothing, not necessarily zero)"

        def fmt(v):
            return f"{v / 1048576:.0f}MiB" if in_mib else f"{v:.2f}"

        lines = []
        for series in results:
            nums = [float(v[1]) for v in series.get("values", [])]
            if not nums:
                continue
            lines.append(
                f"first={fmt(nums[0])} last={fmt(nums[-1])} "
                f"min={fmt(min(nums))} max={fmt(max(nums))}"
            )
        return "\n".join(lines) or "NO DATA"
    except Exception as e:
        return f"Prometheus query failed: {e}"

@tool
def get_memory_trend(pod_name: str, namespace: str = "default", window_minutes: int = 15) -> str:
    """Returns RAM usage for a pod over the trailing window, alongside request
    rate for the same window, so growth can be judged against load rather than
    in isolation. Use this to distinguish genuine memory need (grows with load,
    plateaus) from a leak (grows regardless of load, never drops)."""
    if not _SAFE_NAME.match(pod_name or ""):
        return f"Error: invalid pod_name '{pod_name}'."

    sel = f'pod="{pod_name}",namespace="{namespace}"'

    # Only the app container, not the pod cgroup or pause container
    mem_query = f'container_memory_working_set_bytes{{{sel},container="backend"}}'

    # Works with either metric; the first is used if it exists
    load_query = (
        f'sum(rate(http_requests_total{{{sel}}}[1m])) '
        f'or sum(rate(http_request_duration_seconds_count{{{sel}}}[1m]))'
    )

    # Your own leak-vs-load gauges
    buffers_query = f'app_unbounded_buffers_total{{{sel}}}'
    cache_query = f'app_bounded_cache_entries{{{sel}}}'
    
    
    load = _query_prometheus_range(load_query, window_minutes)
    mem = _query_prometheus_range(mem_query, window_minutes, in_mib=True)
    buffers = _query_prometheus_range(buffers_query, window_minutes)
    cache = _query_prometheus_range(cache_query, window_minutes)

    return (
        f"Request rate (req/s):\n{load}\n\n"
        f"Memory:\n{mem}\n\n"
        f"Unbounded buffers (leak indicator):\n{buffers}\n\n"
        f"Bounded cache entries (genuine-load indicator):\n{cache}"
    )
        
    
    
    
    
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