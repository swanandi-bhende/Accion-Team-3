from typing import Any
from pydantic import BaseModel, Field

class MarketContext(BaseModel):
    conditions: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    timestamp: int | None = None
