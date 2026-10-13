from pydantic import BaseModel, Field
from .agent_context import AgentDecisionContext
from ..models.market import MarketContext
from ..models.decision import DecisionRecord

class DecisionContext(BaseModel):
    agent: AgentDecisionContext
    market: MarketContext | None = None
    recent_history: list[DecisionRecord] = Field(default_factory=list)
