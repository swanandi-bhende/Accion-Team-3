from dataclasses import dataclass

@dataclass
class DecisionModelConfiguration:
    provider: str | None = None
    model_name: str | None = None
    temperature: float = 0.0
    enabled: bool = False
