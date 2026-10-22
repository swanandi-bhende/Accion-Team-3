from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decision_engine.models.task import MarketplaceTask

class TaskMarketplaceService(ABC):
    @abstractmethod
    async def get_available_tasks(self, agent_id: str) -> list[MarketplaceTask]:
        raise NotImplementedError
