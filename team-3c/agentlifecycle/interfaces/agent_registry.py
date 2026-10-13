from abc import ABC, abstractmethod

class AgentContextSource(ABC):
    @abstractmethod
    async def get_agent_context(self, agent_id: str):
        raise NotImplementedError
