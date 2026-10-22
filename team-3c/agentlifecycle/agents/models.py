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

    # Legacy free-text labels. NOT used for readiness or decisions; kept only
    # so existing stored records still load. Do not rely on it in new code.
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Legacy labels only. capability_manifest is the authoritative, "
            "verified capability list."
        ),
    )

    # Authoritative capability list: every entry must have a registered
    # handler or configured remote service (checked during initialize()).
    # The Decision Engine only ever sees this list.
    capability_manifest: list[str] = Field(default_factory=list)

    configuration: dict = Field(default_factory=dict)

    wallet_address: str | None = None

    state: AgentState = AgentState.REGISTERED

    performance: dict = Field(default_factory=dict)