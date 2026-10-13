from typing import Any
from pydantic import BaseModel, Field

class AgentDecisionContext(BaseModel):
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    performance: dict[str, Any] = Field(default_factory=dict)
    active_task_count: int = 0
    max_concurrent_tasks: int = 3
    configuration: dict[str, Any] = Field(default_factory=dict)
    decision_context: dict[str, Any] = Field(default_factory=dict)
