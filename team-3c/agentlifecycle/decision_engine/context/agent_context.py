from pydantic import BaseModel, Field


class AgentDecisionContext(BaseModel):
    """What the Decision Engine (and its prompt) knows about one agent.

    Deliberately small (manual Task 3.4 and Rule 10):
    - no ``performance`` dict, so no invented success rates reach the LLM;
    - no raw ``configuration`` dict, so settings or secrets are never sent
      to the model provider.
    Past behaviour reaches the model only through the agent's own recorded
    decision history.
    """

    agent_id: str
    # Verified capability_manifest entries only (see factory).
    capabilities: list[str] = Field(default_factory=list)
    active_task_count: int = Field(default=0, ge=0)
    max_concurrent_tasks: int = Field(default=3, ge=1)
