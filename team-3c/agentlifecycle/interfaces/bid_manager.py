from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decision_engine.models.decision import DecisionResult

class BidManager(ABC):
    @abstractmethod
    async def submit_decision(self, decision: DecisionResult):
        raise NotImplementedError
