from kubernetes import client, config
from kubernetes.client.rest import ApiException

# def get_gke_client():
#     try:
#         try:
#             config.load_incluster_config()
#         except config.ConfigException:
#             config.load_kube_config()

#         v1 = client.CoreV1Api()
#         return v1
#     except Exception as e:
#         raise RuntimeError(f"Failed to initialize Kubernetes client: {e}")

# # Tool test function
# def list_cluster_pods(namespace="default"):
#     v1 = get_gke_client()
#     try:
#         pods = v1.list_namespaced_pod(namespace=namespace)
#         return [
#             {"name": pod.metadata.name, "status": pod.status.phase}
#             for pod in pods.items
#         ]
#     except ApiException as e:
#         return f"Kubernetes API Error: {e}"



# def get_pod_logs(pod_name: str, namespace: str = "default", tail_lines: int = 20) -> str:
#     """Fetch recent log output for a specific pod."""
#     v1 = get_gke_client()
#     pod_methods = [method for method in dir(v1) if "pod" in method and not method.startswith("_")]
#     print("Pod Methods:\n", pod_methods)

#     # List all Service & Namespace methods
#     service_methods = [m for m in dir(v1) if ("service" in m or "namespace" in m) and not m.startswith("_")]
#     print("\nService & Namespace Methods:\n", service_methods)
#     try:
#         logs = v1.read_namespaced_pod_log(
#             name=pod_name,
#             namespace=namespace,
#             tail_lines=tail_lines
#         )
#         return logs
#     except Exception as e:
#         return f"Error retrieving logs for {pod_name}: {e}"
    
    

# if __name__ == "__main__":
#     pod_list = list_cluster_pods()
#     logs=get_pod_logs(pod_name="logger-error", namespace="default", tail_lines=50)
#     print("Pods in cluster:", pod_list)
#     print("Logs for nginx-app pod:", logs)
    
    
    

