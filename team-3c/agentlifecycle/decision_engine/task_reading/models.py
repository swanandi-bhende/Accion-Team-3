from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskRequirementsAnswer(BaseModel):
    """What the LLM returns for one task (a proposal, checked in code)."""

    task_id: str
    # Catalogue IDs only. Anything else is moved to unsupported in code.
    required_capabilities: list[str]
    # Short phrases for needs no catalogue ID covers. Empty if none.
    unsupported_requirements: list[str]
    reasoning: str


class TaskRequirementsBatch(BaseModel):
    tasks: list[TaskRequirementsAnswer] = Field(default_factory=list)


class RequirementsSource(str, Enum):
    # Requirements came with the task (e.g. simulator listings).
    DECLARED = "declared"
    # The shared Task Reader extracted them from the description.
    EXTRACTED = "extracted"
    # No declared requirements and no description to read, or the reader
    # found nothing it could name.
    UNSPECIFIED = "unspecified"
    # The reader's answer for this task was missing or duplicated.
    UNREADABLE = "unreadable"


class TaskRequirements(BaseModel):
    """Validated requirements for one task, shared by every agent."""

    task_id: str
    source: RequirementsSource
    required_capabilities: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    reasoning: str = ""
