from typing import Any
from pydantic import BaseModel, Field

class MarketplaceTask(BaseModel):
    task_id: str
    creator: str | None = None
    description_hash: str | None = None
    description: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    deadline: int | None = None
    bounty_amount: float = Field(ge=0.0)
    status: str = "open"
    funding_status: str | None = None
    market_context: dict[str, Any] = Field(default_factory=dict)
