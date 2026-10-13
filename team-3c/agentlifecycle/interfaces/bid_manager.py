from abc import ABC, abstractmethod
from decision_engine.models.decision import DecisionResult

class BidManager(ABC):
    @abstractmethod
    async def submit_decision(self, decision: DecisionResult):
        raise NotImplementedError
