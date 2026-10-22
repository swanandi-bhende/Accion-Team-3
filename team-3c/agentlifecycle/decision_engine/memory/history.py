from collections import deque
from ..models.decision import DecisionRecord

class DecisionHistory:
    CONTEXT_WINDOW_SIZE = 20

    def __init__(self, max_size: int = CONTEXT_WINDOW_SIZE):
        self._records = deque(maxlen=max_size)

    def add(self, record: DecisionRecord) -> None:
        self._records.append(record)

    def recent(self) -> list[DecisionRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
