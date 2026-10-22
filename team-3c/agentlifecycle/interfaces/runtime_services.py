"""Runtime-facing contracts for services injected into a MarketAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from agents.models import AgentDefinition
    from decision_engine.models.decision import DecisionResult
    from decision_engine.models.task import MarketplaceTask


class DecisionEngineService(Protocol):
    """Evaluate the tasks returned by the marketplace adapter.

    One instance belongs to exactly one agent: it holds that agent's private
    context and decision history.
    """

    @property
    def ready(self) -> bool:
        ...

    async def evaluate_tasks(
        self,
        tasks: list[MarketplaceTask],
    ) -> list[DecisionResult]:
        ...


class DecisionEngineFactoryService(Protocol):
    """Build a private Decision Engine for one agent.

    Shared parts (model client, prompts, default policy) live in the factory;
    private parts (agent context, decision history) are created per agent.
    """

    def create(
        self,
        definition: AgentDefinition,
    ) -> DecisionEngineService:
        ...


class HistoryEventWriter(Protocol):
    """Persist an event produced by runtime or execution services."""

    async def write_event(
        self,
        event: Mapping[str, object],
    ) -> None:
        ...


class ExecutionDispatcher(Protocol):
    """Dispatch an awarded task to its configured execution service."""

    async def dispatch(
        self,
        task: MarketplaceTask,
    ) -> object:
        ...


class CapabilityRegistry(Protocol):
    """Check and invoke registered capability handlers or usable services."""

    async def is_capability_available(
        self,
        capability: str,
    ) -> bool:
        ...

    async def invoke_capability(
        self,
        capability: str,
        payload: object,
    ) -> object:
        ...
