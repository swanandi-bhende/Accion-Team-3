from abc import ABC, abstractmethod
from langchain_core.language_models.chat_models import BaseChatModel

class DecisionModelProvider(ABC):
    @abstractmethod
    def get_model(self) -> BaseChatModel:
        raise NotImplementedError

class ConfiguredDecisionModelProvider(DecisionModelProvider):
    def __init__(self, model: BaseChatModel):
        self._model = model

    def get_model(self) -> BaseChatModel:
        return self._model
