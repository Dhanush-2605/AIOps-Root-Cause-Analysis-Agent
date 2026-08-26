from typing import TypedDict, List, Annotated, Sequence
from pydantic import BaseModel, Field
import operator
from langchain_core.messages import BaseMessage


class K8sAction(BaseModel):
    action_type: str = Field(description="Must be one of: delete_pod, scale_deployment, scale_statefulset, restart_deployment, increase_memory_limit, increase_cpu_limit, kill_idle_db_connections")
    target_name: str = Field(description="Name of the specific pod, deployment, or statefulset.")
    namespace: str = Field(default="default", description="The Kubernetes namespace.")
    value: str = Field(default="", description="Value for scaling (e.g., '3') or limits (e.g., '512Mi'). Leave empty if not applicable.")


class RemediationPlan(BaseModel):
    root_cause: str = Field(description="A few sentence summary of why the issue occurred based on the logs/metrics.")
    severity: str = Field(description="Must be one of: Low, Medium, High, Critical")
    proposed_actions: List[K8sAction] = Field(description="Structured Kubernetes actions to safely remediate the issue.")
    requires_human_approval: bool = Field(default=True)

class CriticReview(BaseModel):
    is_safe: bool = Field(description="True if the commands are safe to run, False if they are destructive or incorrect.")
    feedback: str = Field(description="If unsafe, explain exactly why so the Proposer can fix it.")



class AgentState(TypedDict):
    # Incident Initiation
    alert_payload: dict                      # The raw JSON from Prometheus Alertmanager
    
    # Investigator Loop
    messages: Annotated[Sequence[BaseMessage], operator.add] # Chat history & tool calls
    context_gathered: str                    # Accumulated logs and metrics from tools
    
    # Proposer & Critic
    proposed_plan: RemediationPlan           # The structured output from the LLM
    critic_feedback: str                     # Notes from the critic if the plan is rejected
    
    # Execution & Verification
    execution_logs: str                      # Terminal output after running the commands
    verification_passed: bool                # Did the Prometheus metric recover?
    verification_attempts: int               # Counter to prevent infinite loops (max 3)