from typing import Any
from pydantic import BaseModel, Field


class TaskDecision(BaseModel):
    """One task evaluation as returned by the LLM (a proposal, not authority)."""

    task_id: str
    match_score: float = Field(ge=0.0, le=1.0)
    suitable: bool
    # None when the model cannot support an estimate from the given facts.
    estimated_completion_seconds: int | None = Field(default=None, ge=0)
    capability_gap: list[str] = Field(default_factory=list)
    # Required: every decision must come with its rationale.
    reasoning: str


class DecisionBatch(BaseModel):
    decisions: list[TaskDecision] = Field(default_factory=list)


class DecisionResult(BaseModel):
    """Final, policy-checked decision for one task.

    Suitability only. The Decision Engine never outputs a price: bid
    amounts are calculated by the separate Bidding Engine (WP3).
    ``suitable`` is True only when the model recommended the task AND every
    hard rule passed.
    """

    task_id: str
    suitable: bool
    # None when the task was rejected before reaching the model.
    match_score: float | None = None
    # The model's own recommendation; None when the model never saw the task.
    model_suitable: bool | None = None
    # Values of policy.RejectionReason; empty when suitable is True.
    rejection_reasons: list[str] = Field(default_factory=list)
    # Required capabilities missing from the agent's verified manifest,
    # computed in code (not taken from the model). Includes needs that no
    # catalogue capability covers.
    capability_gap: list[str] = Field(default_factory=list)
    # What the task needs (catalogue IDs) and where that came from:
    # "declared" with the task, "extracted" by the shared Task Reader,
    # "unspecified"/"unreadable", or None if rejected before reading.
    required_capabilities: list[str] = Field(default_factory=list)
    requirements_source: str | None = None
    estimated_completion_seconds: int | None = None
    reasoning: str = ""


class DecisionRecord(BaseModel):
    """One entry in an agent's private decision history."""

    task_id: str
    suitable: bool
    match_score: float | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
