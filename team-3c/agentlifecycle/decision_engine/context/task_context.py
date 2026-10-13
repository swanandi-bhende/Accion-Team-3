from pydantic import BaseModel, Field
from ..models.task import MarketplaceTask

class TaskContext(BaseModel):
    task: MarketplaceTask
    additional_context: dict = Field(default_factory=dict)
