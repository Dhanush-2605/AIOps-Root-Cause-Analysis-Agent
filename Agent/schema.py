from typing import TypedDict, List, Annotated, Sequence
from pydantic import BaseModel, Field
import operator
from langchain_core.messages import BaseMessage



class RemediationPlan(BaseModel):
    root_cause: str = Field(description="A 2-sentence summary of why the issue occurred based on the logs/metrics.")
    severity: str = Field(description="Must be one of: Low, Medium, High, Critical")
    proposed_commands: List[str] = Field(description="Exact kubectl or bash commands to safely remediate the issue.")
    requires_human_approval: bool = Field(default=True, description="Always true for production-altering commands.")

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