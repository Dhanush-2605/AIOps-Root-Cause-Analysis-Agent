from langchain_core.tools import tool
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests
import json
import re

from langchain_google_community import GoogleSearchAPIWrapper


import google.auth
import google.auth.transport.requests

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
def execute_postgres_query(query: str, namespace: str = "default") -> str:
    """Executes a diagnostic SQL query against the Postgres database pod.
    Useful for checking active connections, locks, and long-running queries via pg_stat_activity.
    """
    try:
        
        v1_api, apps_v1_api = get_gke_client()

        
        # Find the Postgres pod (Assuming standard labeling or name)
        pods = v1_api.list_namespaced_pod(namespace=namespace, label_selector="app=postgres")
        if not pods.items:
            # Fallback if label selector fails
            pods = v1_api.list_namespaced_pod(namespace=namespace)
            pg_pods = [p for p in pods.items if "postgres" in p.metadata.name]
            if not pg_pods:
                return "Error: Could not locate a PostgreSQL pod in this namespace."
            target_pod = pg_pods[0].metadata.name
        else:
            target_pod = pods.items[0].metadata.name

        # Execute the query using psql via kubectl exec
        # We wrap the query in quotes and pass it to the -c flag
        exec_cmd = [
            "psql", 
            "-U", "postgres", # Assumes default user
            "-c", query
        ]
        
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=target_pod,
            namespace=namespace,
            command=exec_cmd,
            stderr=True, stdin=False, stdout=True, tty=False
        )
        return f"Query Results:\n{resp}"
    except Exception as e:
        return f"Error executing Postgres query: {str(e)}"

# @tool
# def query_prometheus(promql_query: str) -> str:
#     """
#     Executes a PromQL query against the internal Prometheus server.
#     Use this to check CPU, RAM, network latency, or historical trends.
    
#     Args:
#         promql_query: A valid Prometheus query string.
#     """

#     PROMETHEUS_URL = "http://prometheus-main.monitoring.svc.cluster.local:9090/api/v1/query"
    
#     try:
#         response = requests.get(PROMETHEUS_URL, params={'query': promql_query}, timeout=10)
#         response.raise_for_status()
#         data = response.json()
        
#         results = data.get('data', {}).get('result', [])
#         if not results:
#             return f"Prometheus returned no data for query: {promql_query}"
            
#         # Format the output so the LLM can easily read it
#         formatted_results = []
#         for result in results:
#             metric_labels = result.get('metric', {})
#             value = result.get('value', [None, "No Value"])[1]
#             formatted_results.append(f"Labels: {metric_labels} | Value: {value}")
            
#         return "\n".join(formatted_results)
#     except requests.exceptions.RequestException as e:
#         return f"Failed to reach Prometheus API: {str(e)}"