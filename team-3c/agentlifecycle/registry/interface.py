"""Abstract persistence boundary for Market Agents."""

from abc import ABC, abstractmethod

from agents.models import AgentDefinition


class AgentRegistry(ABC):
    """Storage contract used by the Agent Runtime."""

    @abstractmethod
    async def register(self, agent: AgentDefinition) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, agent_id: str) -> AgentDefinition | None:
        raise NotImplementedError

    @abstractmethod
    async def list(self) -> list[AgentDefinition]:
        raise NotImplementedError

    @abstractmethod
    async def update(self, agent: AgentDefinition) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, agent_id: str) -> None:
        raise NotImplementedError