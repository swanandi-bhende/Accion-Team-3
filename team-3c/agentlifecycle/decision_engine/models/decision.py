from typing import Any
from pydantic import BaseModel, Field

class TaskDecision(BaseModel):
    task_id: str
    match_score: float = Field(ge=0.0, le=1.0)
    suitable: bool
    bid_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    estimated_completion_seconds: int | None = Field(default=None, ge=0)
    capability_gap: list[str] = Field(default_factory=list)
    reasoning: str = ""

class DecisionBatch(BaseModel):
    decisions: list[TaskDecision] = Field(default_factory=list)

class DecisionResult(BaseModel):
    task_id: str
    match_score: float
    suitable: bool
    recommended_bid: float | None = None
    estimated_completion_seconds: int | None = None
    capability_gap: list[str] = Field(default_factory=list)
    reasoning: str = ""

class DecisionRecord(BaseModel):
    task_id: str
    match_score: float
    suitable: bool
    recommended_bid: float | None = None
    outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
