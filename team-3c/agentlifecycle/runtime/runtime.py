from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Dict

from agents.models import AgentDefinition, AgentState
from registry.interface import AgentRegistry

if TYPE_CHECKING:
    from interfaces.bid_manager import BidManager
    from interfaces.marketplace import TaskMarketplaceService
    from interfaces.runtime_services import (
        CapabilityRegistry,
        DecisionEngineFactoryService,
        DecisionEngineService,
        ExecutionDispatcher,
        HistoryEventWriter,
    )


class MarketAgent:
    """
    Runtime representation of one Market Agent.

    This class manages the lifecycle of the agent.
    It does NOT contain the actual AI intelligence yet.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        registry: AgentRegistry,
        *,
        marketplace: TaskMarketplaceService,
        decision_engine: DecisionEngineService,
        bidding_engine: BidManager,
        capability_registry: CapabilityRegistry,
        history_writer: HistoryEventWriter,
        execution_dispatcher: ExecutionDispatcher,
    ):
        self.definition = definition
        self.registry = registry
        self.marketplace = marketplace
        self.decision_engine = decision_engine
        self.bidding_engine = bidding_engine
        self.capability_registry = capability_registry
        self.history_writer = history_writer
        self.execution_dispatcher = execution_dispatcher

        self.agent_id = definition.agent_id

        # Runtime state
        self.state = definition.state

        # Background decision-cycle task
        self._decision_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._cycle_in_progress = asyncio.Event()
        self._cycle_in_progress.set()

        # Tells the decision cycle when it should completely stop
        self._stop_event = asyncio.Event()

        # Tells the decision cycle whether it is allowed to run
        # It starts SET, meaning the agent is not paused.
        self._resume_event = asyncio.Event()
        self._resume_event.set()

        # Current backoff after a retryable error (None = no recent error).
        self._retry_delay: float | None = None

    async def _set_state(self, new_state: AgentState):
        """
        Change the runtime state AND persist the same state
        in the Agent Registry.
        """

        self.state = new_state
        self.definition.state = new_state

        await self.registry.update(self.definition)

        print(
            f"[{self.agent_id}] State -> {new_state.value}"
        )

    async def initialize(self):
        """
        Validate the agent definition and required service readiness.
        """

        if self.state != AgentState.REGISTERED:
            raise RuntimeError(
                f"Cannot initialize agent in state {self.state}"
            )

        print(f"[{self.agent_id}] Initializing...")

        try:
            definition_data = self.definition.model_dump()
            await self._set_state(AgentState.INITIALIZING)
            definition = AgentDefinition.model_validate(
                definition_data
            )
            if definition.state != AgentState.REGISTERED:
                raise RuntimeError(
                    "Agent definition must be in REGISTERED state"
                )
            self._require_service(
                self.marketplace,
                "get_available_tasks",
                "Marketplace",
            )
            self._require_service(
                self.decision_engine,
                "evaluate_tasks",
                "Decision Engine",
            )
            if getattr(self.decision_engine, "ready", None) is not True:
                raise RuntimeError("Decision Engine is not ready")

            self._require_service(
                self.bidding_engine,
                "submit_decision",
                "Bidding Engine",
            )
            self._require_service(
                self.capability_registry,
                "is_capability_available",
                "Capability Registry",
            )
            self._require_service(
                self.history_writer,
                "write_event",
                "History/Event Writer",
            )
            self._require_service(
                self.execution_dispatcher,
                "dispatch",
                "Execution Dispatcher",
            )

            for capability in definition.capability_manifest:
                is_available = (
                    await self.capability_registry.is_capability_available(
                        capability
                    )
                )
                if is_available is not True:
                    raise RuntimeError(
                        f"No usable handler or remote service is registered "
                        f"for capability: {capability}"
                    )

            await self._set_state(AgentState.INACTIVE)
            await self.history_writer.write_event(
                {
                    "event_type": "initialization_complete",
                    "agent_id": self.agent_id,
                    "state": AgentState.INACTIVE.value,
                }
            )
        except Exception:
            await self._set_state(AgentState.ERROR)
            raise

        print(
            f"[{self.agent_id}] Initialization complete."
        )

    @staticmethod
    def _require_service(
        service: object,
        method_name: str,
        service_name: str,
    ) -> None:
        if service is None or not callable(getattr(service, method_name, None)):
            raise RuntimeError(
                f"{service_name} is missing or does not implement "
                f"{method_name}"
            )

    async def activate(self):
        """
        Start the agent's autonomous decision cycle.
        """

        async with self._lifecycle_lock:
            if self.state != AgentState.INACTIVE:
                raise RuntimeError(
                    f"Cannot activate agent in state {self.state}"
                )

            print(f"[{self.agent_id}] Activating...")

            self._stop_event.clear()

            # Make sure the cycle is allowed to run.
            self._resume_event.set()

            await self._set_state(
                AgentState.ACTIVE
            )

            self._decision_task = asyncio.create_task(
                self._decision_cycle()
            )

            print(
                f"[{self.agent_id}] ACTIVE"
            )

    async def _decision_cycle(self):
        """
        Poll the marketplace and route available tasks through the
        injected decision and bidding services.
        """

        while not self._stop_event.is_set():

            # --------------------------------------------------
            # If the agent is paused, wait here.
            #
            # The cycle will NOT perform decision work while
            # this event is cleared.
            # --------------------------------------------------

            await self._resume_event.wait()

            # Shutdown may have happened while we were waiting.
            if self._stop_event.is_set():
                break

            self._cycle_in_progress.clear()
            try:
                tasks = await self.marketplace.get_available_tasks(
                    self.agent_id
                )

                if not tasks:
                    await self.history_writer.write_event(
                        {
                            "event_type": "decision_cycle_completed",
                            "agent_id": self.agent_id,
                            "tasks_found": 0,
                            "decisions_made": 0,
                            "bids_submitted": 0,
                        }
                    )
                else:
                    decisions = await self.decision_engine.evaluate_tasks(
                        tasks
                    )
                    bids_submitted = 0

                    for decision in decisions:
                        await self.history_writer.write_event(
                            {
                                "event_type": "decision_evaluated",
                                "agent_id": self.agent_id,
                                "decision": decision.model_dump(),
                            }
                        )

                        if decision.suitable:
                            await self.bidding_engine.submit_decision(
                                decision
                            )
                            bids_submitted += 1
                            await self.history_writer.write_event(
                                {
                                    "event_type": "bid_submission_completed",
                                    "agent_id": self.agent_id,
                                    "task_id": decision.task_id,
                                    "decision": decision.model_dump(),
                                }
                            )

                    await self.history_writer.write_event(
                        {
                            "event_type": "decision_cycle_completed",
                            "agent_id": self.agent_id,
                            "tasks_found": len(tasks),
                            "decisions_made": len(decisions),
                            "bids_submitted": bids_submitted,
                        }
                    )
            except Exception as error:
                if getattr(error, "retryable", False) is not True:
                    print(
                        f"[{self.agent_id}] Decision cycle failed: {error}"
                    )
                    await self._set_state(AgentState.ERROR)
                    raise
                # Temporary problem (e.g. LLM rate limit or timeout): keep
                # the agent ACTIVE, skip this cycle and retry with backoff.
                retry_after = self._next_retry_delay()
                retry_error = error
            else:
                retry_after = None
                self._retry_delay = None
            finally:
                self._cycle_in_progress.set()

            if retry_after is not None:
                await self._record_skipped_cycle(retry_error, retry_after)
                await self._sleep_unless_stopped(retry_after)
                continue

            await asyncio.sleep(2)

    # Backoff for retryable errors: 2s, 4s, 8s, 16s, then 30s max.
    RETRY_BASE_SECONDS = 2.0
    RETRY_MAX_SECONDS = 30.0

    def _next_retry_delay(self) -> float:
        previous = self._retry_delay
        if previous is None:
            delay = self.RETRY_BASE_SECONDS
        else:
            delay = min(previous * 2, self.RETRY_MAX_SECONDS)
        self._retry_delay = delay
        return delay

    async def _record_skipped_cycle(self, error: Exception, retry_after: float):
        print(
            f"[{self.agent_id}] Temporary problem "
            f"({type(error).__name__}); skipping this cycle, "
            f"retrying in {retry_after:.0f}s"
        )
        try:
            await self.history_writer.write_event(
                {
                    "event_type": "decision_cycle_skipped",
                    "agent_id": self.agent_id,
                    "error_type": type(error).__name__,
                    "retry_in_seconds": retry_after,
                }
            )
        except Exception:
            # Same rule as inside the cycle: a broken history writer is not
            # a temporary LLM problem, so the agent stops visibly.
            await self._set_state(AgentState.ERROR)
            raise

    async def _sleep_unless_stopped(self, seconds: float):
        """Sleep in short steps so shutdown() never waits for a long backoff."""
        remaining = seconds
        while remaining > 0 and not self._stop_event.is_set():
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def pause(self):
        """
        Prevent another cycle from starting and wait for current work to finish.
        """

        async with self._lifecycle_lock:
            if self.state != AgentState.ACTIVE:
                raise RuntimeError(
                    f"Cannot pause agent in state {self.state}"
                )

            print(f"[{self.agent_id}] Pausing...")

            # Clearing this before waiting prevents the loop starting another cycle.
            self._resume_event.clear()
            await self._cycle_in_progress.wait()

            if self.state != AgentState.ACTIVE:
                task = self._decision_task
                if self.state == AgentState.ERROR and task is not None:
                    await asyncio.wait({task})
                    error = task.exception()
                    if error is not None:
                        raise error
                raise RuntimeError(
                    f"Cannot pause agent in state {self.state}"
                )

            await self._set_state(
                AgentState.PAUSED
            )

            print(
                f"[{self.agent_id}] PAUSED"
            )

    async def resume(self):
        """
        Resume a paused decision cycle.
        """

        async with self._lifecycle_lock:
            if self.state != AgentState.PAUSED:
                raise RuntimeError(
                    f"Cannot resume agent in state {self.state}"
                )

            print(f"[{self.agent_id}] Resuming...")

            # Allow the decision cycle to continue.
            self._resume_event.set()

            await self._set_state(
                AgentState.ACTIVE
            )

            if (
                self._decision_task is None
                or self._decision_task.done()
            ):
                self._decision_task = asyncio.create_task(
                    self._decision_cycle()
                )

            print(
                f"[{self.agent_id}] ACTIVE"
            )

    async def shutdown(self):
        """
        Gracefully stop the agent and its background cycle.
        """

        async with self._lifecycle_lock:
            if self.state not in (
                AgentState.ACTIVE,
                AgentState.PAUSED,
            ):
                raise RuntimeError(
                    f"Cannot shutdown agent in state {self.state}"
                )

            print(
                f"[{self.agent_id}] Shutting down..."
            )

            # Prevent another cycle from starting while the current one finishes.
            self._resume_event.clear()

            await self._set_state(
                AgentState.STOPPING
            )

            # Release a paused worker and tell it to exit at the next safe boundary.
            self._stop_event.set()
            self._resume_event.set()

            task = self._decision_task
            if task is not None:
                try:
                    await task
                finally:
                    if task.done():
                        self._decision_task = None

            await self._set_state(
                AgentState.INACTIVE
            )

            print(
                f"[{self.agent_id}] INACTIVE"
            )


class AgentRuntime:
    """
    Manages multiple MarketAgent instances.

    The Runtime is responsible for orchestration and
    lifecycle management.

    Each loaded agent gets its own Decision Engine, built by the injected
    factory: shared model/prompts/policy, private context and history.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        marketplace: TaskMarketplaceService,
        decision_engine_factory: DecisionEngineFactoryService,
        bidding_engine: BidManager,
        capability_registry: CapabilityRegistry,
        history_writer: HistoryEventWriter,
        execution_dispatcher: ExecutionDispatcher,
    ):
        self.registry = registry
        self.marketplace = marketplace
        self.decision_engine_factory = decision_engine_factory
        self.bidding_engine = bidding_engine
        self.capability_registry = capability_registry
        self.history_writer = history_writer
        self.execution_dispatcher = execution_dispatcher

        self.agents: Dict[str, MarketAgent] = {}

    async def load_agent(
        self,
        agent_id: str,
    ) -> MarketAgent:
        """
        Load an existing agent definition from Redis
        and create its runtime representation.
        """

        if agent_id in self.agents:
            return self.agents[agent_id]

        definition = await self.registry.get(
            agent_id
        )

        if definition is None:
            raise ValueError(
                f"Agent not found in registry: {agent_id}"
            )

        # A fresh, private engine for this agent only.
        decision_engine = self.decision_engine_factory.create(definition)

        agent = MarketAgent(
            definition,
            self.registry,
            marketplace=self.marketplace,
            decision_engine=decision_engine,
            bidding_engine=self.bidding_engine,
            capability_registry=self.capability_registry,
            history_writer=self.history_writer,
            execution_dispatcher=self.execution_dispatcher,
        )

        self.agents[agent_id] = agent

        print(
            f"[Runtime] Loaded agent: {agent_id}"
        )

        return agent

    async def initialize_agent(
        self,
        agent_id: str,
    ):
        agent = await self.load_agent(
            agent_id
        )

        await agent.initialize()

    async def activate_agent(
        self,
        agent_id: str,
    ):
        agent = await self.load_agent(
            agent_id
        )

        await agent.activate()

    async def pause_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.pause()

    async def resume_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.resume()

    async def shutdown_agent(
        self,
        agent_id: str,
    ):
        agent = self.agents.get(
            agent_id
        )

        if agent is None:
            raise ValueError(
                f"Agent is not loaded: {agent_id}"
            )

        await agent.shutdown()

    def get_agent(
        self,
        agent_id: str,
    ) -> MarketAgent | None:
        return self.agents.get(agent_id)

    def get_all_agents(
        self,
    ) -> list[MarketAgent]:
        return list(self.agents.values())