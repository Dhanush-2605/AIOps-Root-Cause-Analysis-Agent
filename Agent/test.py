import requests

session = requests.Session()
session.trust_env = False  # Bypasses local proxy settings

response = session.post("http://127.0.0.1:8000/evaluate-prompt", json={"prompt": "Hello"})
print(response.json())