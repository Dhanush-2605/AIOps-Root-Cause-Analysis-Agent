from typing import TypedDict, List, Annotated, Sequence, Literal, Optional
from pydantic import BaseModel, Field
import operator
from langchain_core.messages import BaseMessage


class K8sAction(BaseModel):
    action_type: Literal[
        "scale_deployment",
        "delete_pod",
        "restart_deployment",
        "increase_memory_limit",
        "increase_cpu_limit",
        "kill_idle_transaction",
        "abort_hanging_requests"
             
    ] = Field(
        ...,
        description="The precise remediation action to execute. Must match exactly one of the permitted options."
    )
    
    target_name: str = Field(
        ..., 
        description="The exact name of the specific pod, deployment, or service to target."
    )
    
    namespace: str = Field(
        default="default", 
        description="The Kubernetes namespace where the target resource lives."
    )
    
    value: Optional[str] = Field(
        None, 
        description=(
            "Action parameters: "
            "- For scaling/limits: e.g., '3' or '512Mi'. "
            "- For 'kill_blocking_transaction': the blocking PID, as found by "
            "- get_blocking_transactions during investigation (e.g. '4521'). "            
            "- For 'abort_hanging_requests': max duration in seconds (e.g., '30'). "
            "- Leave null if the action requires no parameters."
        )
    )
    
    reasoning: str = Field(
        ..., 
        description="A brief explanation of why this specific action safely resolves the root cause. This will be shown to human SREs for approval."
    )

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
    
    
    incident_start_time: str   # Alert timestamp or start of workflow
    incident_end_time: str     # Post-execution completion timestamp
    post_mortem_md: str        # Generated Markdown Post-Mortem
    github_pr_url: str
    
    hitl_route: str # 'execute', 'abort', or 'replan'