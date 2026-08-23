from langchain_core.tools import tool
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests
import json
import re



import google.auth
import google.auth.transport.requests

def get_gke_auth_token():
    """Fetches a Google OAuth token for GKE Workload Identity."""
    try:
        credentials, _ = google.auth.default(
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        return credentials.token
    except Exception as e:
        print(f"Failed to fetch Google Auth token: {e}")
        return None

try:
    # 1. Try standard in-cluster (Works if NO Workload Identity)
    config.load_incluster_config()
    v1_api = client.CoreV1Api()
    
    # 2. If we are in GKE with Workload Identity, we must inject the token manually
    token = get_gke_auth_token()
    if token:
        v1_api.api_client.configuration.api_key['authorization'] = f"Bearer {token}"
        
    # Initialize Apps API for scaling deployments/statefulsets
    apps_v1_api = client.AppsV1Api(v1_api.api_client)
        
except config.ConfigException:
    try:
        # 3. Fallback for local testing (reading ~/.kube/config)
        config.load_kube_config()
        v1_api = client.CoreV1Api()
        apps_v1_api = client.AppsV1Api()
    except Exception as e:
        print(f"Warning: Could not configure kubernetes client. {e}")
        v1_api = client.CoreV1Api() # Initialize empty so it doesn't crash on import
        apps_v1_api = client.AppsV1Api()



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
        # Fetch the raw logs
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
def query_prometheus(promql_query: str) -> str:
    """
    Executes a PromQL query against the internal Prometheus server.
    Use this to check CPU, RAM, network latency, or historical trends.
    
    Args:
        promql_query: A valid Prometheus query string.
    """

    PROMETHEUS_URL = "http://prometheus-main.monitoring.svc.cluster.local:9090/api/v1/query"
    
    try:
        response = requests.get(PROMETHEUS_URL, params={'query': promql_query}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = data.get('data', {}).get('result', [])
        if not results:
            return f"Prometheus returned no data for query: {promql_query}"
            
        # Format the output so the LLM can easily read it
        formatted_results = []
        for result in results:
            metric_labels = result.get('metric', {})
            value = result.get('value', [None, "No Value"])[1]
            formatted_results.append(f"Labels: {metric_labels} | Value: {value}")
            
        return "\n".join(formatted_results)
    except requests.exceptions.RequestException as e:
        return f"Failed to reach Prometheus API: {str(e)}"