from langchain_core.tools import tool
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests
import json
import re
import os
from kubernetes.stream import stream

from langchain_google_community import GoogleSearchAPIWrapper


import google.auth
import google.auth.transport.requests
BACKEND_ADMIN_URL = os.environ.get("BACKEND_ADMIN_URL", "http://backend-service:8080")


# def get_gke_auth_token():
#     """Fetches a Google OAuth token for GKE Workload Identity."""
#     try:
#         credentials, _ = google.auth.default(
#             scopes=['https://www.googleapis.com/auth/cloud-platform']
#         )
#         auth_req = google.auth.transport.requests.Request()
#         credentials.refresh(auth_req)
#         return credentials.token
#     except Exception as e:
#         print(f"Failed to fetch Google Auth token: {e}")
#         return None

# try:
#     # 1. Try standard in-cluster (Works if NO Workload Identity)
#     config.load_incluster_config()
#     v1_api = client.CoreV1Api()
    
#     # 2. If we are in GKE with Workload Identity, we must inject the token manually
#     token = get_gke_auth_token()
#     if token:
#         v1_api.api_client.configuration.api_key['authorization'] = f"Bearer {token}"
        
#     # Initialize Apps API for scaling deployments/statefulsets
#     apps_v1_api = client.AppsV1Api(v1_api.api_client)
        
# except config.ConfigException:
#     try:
#         # 3. Fallback for local testing (reading ~/.kube/config)
#         config.load_kube_config()
#         v1_api = client.CoreV1Api()
#         apps_v1_api = client.AppsV1Api()
#     except Exception as e:
#         print(f"Warning: Could not configure kubernetes client. {e}")
#         v1_api = client.CoreV1Api() # Initialize empty so it doesn't crash on import
#         apps_v1_api = client.AppsV1Api()



def get_gke_client():
    try:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        
        # MUST return both as a tuple
        return v1, apps_v1
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Kubernetes client: {e}")
    



try:
    search_wrapper = GoogleSearchAPIWrapper()
except Exception as e:
    print(f"Warning: Google Search API not configured. Missing env vars. {e}")
    search_wrapper = None

# @tool
# def search_google(query: str) -> str:
#     """
#     Searches Google for troubleshooting steps, Kubernetes documentation, or specific error messages.
#     Use this tool ONLY when you encounter an unfamiliar error code or need external documentation
#     to formulate a fix that isn't covered by internal tools or SOPs.
    
#     Args:
#         query: The exact error message or technical question to search for.
#     """
#     if not search_wrapper:
#         return "Error: Google Search tool is not configured on the server. Cannot perform web search."
    
#     try:
#         # Run the search and return the top snippets
#         return search_wrapper.run(query)
#     except Exception as e:
#         return f"Google Search failed: {str(e)}"
    
    

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
        
        v1_api, apps_v1_api = get_gke_client()
        logs = v1_api.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)
       
        error_patterns = re.compile(r"(error|fatal|exception|fail|timeout|oomkilled|denied)", re.IGNORECASE)
        
        filtered_logs = [line for line in logs.split('\n') if error_patterns.search(line)]
        
        if not filtered_logs:
            return f"Logs fetched successfully. No explicit error keywords found in the last {tail_lines} lines. The pod might be stuck rather than crashing."
            
        return "Filtered Error Logs:\n" + "\n".join(filtered_logs)
        
    except ApiException as e:
        return f"Kubernetes API Error: Could not fetch logs for pod {pod_name}. Details: {e.reason}"

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
            "container_statuses": []
        }
        
        if pod.status.container_statuses:
            for container in pod.status.container_statuses:
                state_dict = {}
                if container.state.waiting:
                    state_dict = {"state": "Waiting", "reason": container.state.waiting.reason, "message": container.state.waiting.message}
                elif container.state.terminated:
                    state_dict = {"state": "Terminated", "reason": container.state.terminated.reason, "exit_code": container.state.terminated.exit_code}
                else:
                    state_dict = {"state": "Running"}
                    
                status_info["container_statuses"].append({
                    "name": container.name,
                    "ready": container.ready,
                    "restart_count": container.restart_count,
                    "current_state": state_dict
                })
                
        return json.dumps(status_info, indent=2)
    except ApiException as e:
        return f"Kubernetes API Error: {e.reason}"

@tool
def fetch_hanging_requests(threshold_sec: int = 30) -> str:
    """Lists in-flight HTTP requests older than threshold_sec WITHOUT aborting 
    them — for deciding whether an abort is warranted, not performing it."""
    try:
        resp = requests.get(f"{BACKEND_ADMIN_URL}/admin/requests/list", params={"threshold": threshold_sec}, timeout=10)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2)
    except Exception as e:
        return f"Failed to fetch hanging requests: {str(e)}"






def _run_psql_readonly(query: str, namespace: str = "default") -> str:
    """Internal helper — never exposed directly as a tool. Only called by the 
    fixed diagnostic queries below, so the LLM can never construct arbitrary SQL."""
    v1_api, _ = get_gke_client()
    pods = v1_api.list_namespaced_pod(namespace=namespace, label_selector="app=postgres")
    if not pods.items:
        pods_all = v1_api.list_namespaced_pod(namespace=namespace)
        pg_pods = [p for p in pods_all.items if "postgres" in p.metadata.name]
        if not pg_pods:
            return "Error: Could not locate a PostgreSQL pod in this namespace."
        target_pod = pg_pods[0].metadata.name
    else:
        target_pod = pods.items[0].metadata.name

    exec_cmd = ["psql", "-U", "postgres", "-c", query]
    try:
        # bug fix: use v1_api, not the undefined global v1
        resp = stream(
            v1_api.connect_get_namespaced_pod_exec,
            name=target_pod, namespace=namespace, command=exec_cmd,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        return resp
    except Exception as e:
        return f"Error executing diagnostic query: {str(e)}"



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
    return _run_psql_readonly(query, namespace)
