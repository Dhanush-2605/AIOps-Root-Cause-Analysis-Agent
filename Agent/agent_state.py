from typing import TypedDict, List, Annotated, Sequence, Literal, Optional
from pydantic import BaseModel, Field
import operator
from langchain_core.messages import BaseMessage


# NOTE: these Field descriptions are sent to the LLM as part of the structured-output
# schema on every proposer/critic call, so they are kept short on purpose.
class K8sAction(BaseModel):
    action_type: Literal[
        "scale_deployment",
        "delete_pod",
        "restart_deployment",
        "increase_memory_limit",
        "increase_cpu_limit",
        "kill_blocking_transaction",
        "abort_hanging_requests",
    ] = Field(..., description="Action to execute; must be one of the permitted options.")

    target_name: str = Field(..., description="Exact pod, deployment or service name.")

    namespace: str = Field(default="default", description="Kubernetes namespace.")

    value: Optional[str] = Field(
        None,
        description=(
            "scale_deployment: replica count, e.g. '2'. "
            "increase_memory_limit: e.g. '768Mi'. "
            "increase_cpu_limit: e.g. '600m'. "
            "kill_blocking_transaction: numeric PID from the investigation, e.g. '4521'. "
            "abort_hanging_requests: age threshold in seconds, e.g. '10'. "
            "Otherwise null."
        ),
    )

    reasoning: str = Field(
        ...,
        description="Why this action fixes the root cause, citing evidence. Shown to the human approver.",
    )


class RemediationPlan(BaseModel):
    root_cause: str = Field(description="Short summary of why the issue occurred.")
    severity: Literal["Low", "Medium", "High", "Critical"]
    confidence: Literal["high", "medium", "low", "inconclusive"] = Field(
        ..., description="Confidence in the root cause; 'inconclusive' if no clear cause."
    )
    proposed_actions: List[K8sAction] = Field(
        description="Actions to remediate; empty if inconclusive."
    )
    requires_human_approval: bool = Field(default=True)


class CriticReview(BaseModel):
    is_safe: bool = Field(description="True if safe to run.")
    feedback: str = Field(description="If unsafe, exactly why, so the proposer can fix it.")


# NOTE: LangGraph silently DROPS any key a node returns that is not declared here.
# Every key returned by any node must be listed below.
class AgentState(TypedDict, total=False):
    # Incident Initiation
    alert_payload: dict  # The raw JSON from Prometheus Alertmanager

    # Investigator Loop
    messages: Annotated[Sequence[BaseMessage], operator.add]  # Chat history & tool calls
    context_gathered: str  # Accumulated logs and metrics from tools
    investigation_iterations: int  # Counter for the investigator hard cap

    # Proposer & Critic
    proposed_plan: RemediationPlan  # The structured output from the LLM
    critic_feedback: str  # Notes from the critic if the plan is rejected
    is_safe: bool  # Critic verdict on the current plan
    critic_attempts: int  # How many times the critic rejected the current plan lineage
    human_feedback: str  # Operator feedback from the HITL step (kept separate from critic_feedback)

    # Execution & Verification
    execution_logs: str  # Terminal output after running the commands
    verification_passed: bool  # Did the Prometheus metric recover?
    verification_attempts: int  # Counter to prevent infinite loops (max 3)

    incident_start_time: str  # Alert timestamp or start of workflow
    incident_end_time: str  # Post-execution completion timestamp
    post_mortem_md: str  # Generated Markdown Post-Mortem
    github_pr_url: str
    github_commit_url: str  # What reporter_node actually returns

    hitl_route: str  # 'execute', 'abort', or 'replan'