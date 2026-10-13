from typing import Any
from pydantic import BaseModel, Field

class NegotiationTerms(BaseModel):
    task_id: str
    offered_price: float = Field(ge=0.0)
    estimated_completion_seconds: int | None = Field(default=None, ge=0)
    terms: dict[str, Any] = Field(default_factory=dict)

class NegotiationDecision(BaseModel):
    task_id: str
    accepted: bool
    revised_price: float | None = Field(default=None, ge=0.0)
    revised_completion_seconds: int | None = Field(default=None, ge=0)
    reasoning: str = ""
