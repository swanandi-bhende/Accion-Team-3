"""Data models for a registered Market Agent."""

from enum import Enum

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    REGISTERED = "REGISTERED"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    INACTIVE = "INACTIVE"
    ERROR = "ERROR"


class AgentDefinition(BaseModel):
    """Persistent definition/metadata for one Market Agent."""

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)

    capabilities: list[str] = Field(default_factory=list)

    configuration: dict = Field(default_factory=dict)

    wallet_address: str | None = None

    state: AgentState = AgentState.REGISTERED

    performance: dict = Field(default_factory=dict)