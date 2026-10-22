import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agents.models import AgentDefinition, AgentState
from interfaces.bid_manager import BidManager
from interfaces.marketplace import TaskMarketplaceService
from interfaces.runtime_services import (
    CapabilityRegistry,
    DecisionEngineFactoryService,
    DecisionEngineService,
    ExecutionDispatcher,
    HistoryEventWriter,
)
from registry.interface import AgentRegistry
from runtime.capability_registry import InMemoryCapabilityRegistry
from runtime.capabilities import analyze_text
from runtime.runtime import AgentRuntime, MarketAgent


REDIS_URL = "redis://localhost:6379"


def _mock_services():
    decision_engine = Mock(spec=DecisionEngineService)
    decision_engine.ready = True
    capability_registry = AsyncMock(spec=CapabilityRegistry)
    capability_registry.is_capability_available.return_value = True

    return {
        "marketplace": Mock(spec=TaskMarketplaceService),
        "decision_engine": decision_engine,
        "bidding_engine": Mock(spec=BidManager),
        "capability_registry": capability_registry,
        "history_writer": AsyncMock(spec=HistoryEventWriter),
        "execution_dispatcher": Mock(spec=ExecutionDispatcher),
    }


def _runtime_services(services=None):
    """AgentRuntime takes a Decision Engine factory instead of one engine."""
    services = dict(services or _mock_services())
    engine = services.pop("decision_engine")
    factory = Mock(spec=DecisionEngineFactoryService)
    factory.create.return_value = engine
    services["decision_engine_factory"] = factory
    return services


async def main():
    import redis.asyncio as redis
    from registry.redis_registry import RedisAgentRegistry

    # --------------------------------------------------
    # 1. Connect to Redis
    # --------------------------------------------------

    client = redis.from_url(
        REDIS_URL,
        decode_responses=True
    )

    await client.ping()

    print("Redis connected.")


    # --------------------------------------------------
    # 2. Create Registry
    # --------------------------------------------------

    registry = RedisAgentRegistry(client)


    # --------------------------------------------------
    # 3. Make sure our test agent exists
    # --------------------------------------------------

    agent_definition = AgentDefinition(
        agent_id="agent_001",
        name="ResearchAgent",
        capabilities=[
            "text_analysis",
            "data_analysis"
        ],
        configuration={
            "decision_interval": 5
        },
        wallet_address="0x123456789"
    )

    existing_agent = await registry.get("agent_001")

    if existing_agent is None:

        await registry.register(agent_definition)

        print("Test agent registered.")

    else:

        print("Test agent already exists.")


    # --------------------------------------------------
    # 4. Create Agent Runtime
    # --------------------------------------------------

    runtime = AgentRuntime(
        registry,
        **_runtime_services(),
    )


    # --------------------------------------------------
    # 5. Load agent from Redis
    # --------------------------------------------------

    agent = await runtime.load_agent("agent_001")

    print(
        f"\nLoaded agent: {agent.agent_id}"
    )

    print(
        f"Initial state: {agent.state}"
    )


    # --------------------------------------------------
    # 6. Initialize agent
    # --------------------------------------------------

    await runtime.initialize_agent("agent_001")

    print(
        f"After initialization: {agent.state}"
    )


    # --------------------------------------------------
    # 7. Activate agent
    # --------------------------------------------------

    await runtime.activate_agent("agent_001")

    print(
        f"After activation: {agent.state}"
    )


    # --------------------------------------------------
    # 8. Let decision cycle run
    # --------------------------------------------------

    print("\nLetting decision cycle run...")

    await asyncio.sleep(6)


    # --------------------------------------------------
    # 9. Pause agent
    # --------------------------------------------------

    await runtime.pause_agent("agent_001")

    print(
        f"After pause: {agent.state}"
    )


    # --------------------------------------------------
    # 10. Resume agent
    # --------------------------------------------------

    await runtime.resume_agent("agent_001")

    print(
        f"After resume: {agent.state}"
    )


    # --------------------------------------------------
    # 11. Let it run again
    # --------------------------------------------------

    await asyncio.sleep(3)


    # --------------------------------------------------
    # 12. Shutdown
    # --------------------------------------------------

    await runtime.shutdown_agent("agent_001")

    print(
        f"After shutdown: {agent.state}"
    )


    # --------------------------------------------------
    # 13. Verify final state
    # --------------------------------------------------

    assert agent.state.value == "INACTIVE"

    print("\n===================================")
    print("RUNTIME TEST PASSED")
    print("===================================")


    # --------------------------------------------------
    # 14. Close Redis connection
    # --------------------------------------------------

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())


class TestMarketAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.definition = AgentDefinition(
            agent_id="test-agent",
            name="Test Agent",
        )
        self.registry = AsyncMock(spec=AgentRegistry)
        self.persisted_states = []

        async def record_state(definition):
            self.persisted_states.append(definition.state)

        self.registry.update.side_effect = record_state
        self.services = _mock_services()

    def test_requires_all_services_and_stores_injected_dependencies(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        self.assertIs(agent.registry, self.registry)
        self.assertIs(agent.marketplace, self.services["marketplace"])
        self.assertIs(agent.decision_engine, self.services["decision_engine"])
        self.assertIs(agent.bidding_engine, self.services["bidding_engine"])
        self.assertIs(
            agent.capability_registry,
            self.services["capability_registry"],
        )
        self.assertIs(agent.history_writer, self.services["history_writer"])
        self.assertIs(
            agent.execution_dispatcher,
            self.services["execution_dispatcher"],
        )

        missing_dispatcher = {
            key: service
            for key, service in self.services.items()
            if key != "execution_dispatcher"
        }
        with self.assertRaises(TypeError):
            MarketAgent(
                self.definition,
                self.registry,
                **missing_dispatcher,
            )

    async def test_existing_lifecycle_transitions_are_preserved(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )
        real_sleep = asyncio.sleep

        async def fast_sleep(_):
            await real_sleep(0)

        with patch("runtime.runtime.asyncio.sleep", new=fast_sleep):
            await agent.initialize()
            await agent.activate()
            await agent.pause()
            await agent.resume()
            await agent.shutdown()

        self.assertEqual(agent.state, AgentState.INACTIVE)
        self.assertEqual(
            self.persisted_states,
            [
                AgentState.INITIALIZING,
                AgentState.INACTIVE,
                AgentState.ACTIVE,
                AgentState.PAUSED,
                AgentState.ACTIVE,
                AgentState.STOPPING,
                AgentState.INACTIVE,
            ],
        )

    async def test_pause_prevents_new_cycles_and_resume_restarts_them(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )
        first_cycle_sleep = asyncio.Event()
        release_first_cycle = asyncio.Event()
        second_cycle = asyncio.Event()
        call_count = 0
        real_sleep = asyncio.sleep

        async def get_tasks(agent_id):
            nonlocal call_count
            self.assertEqual(agent_id, self.definition.agent_id)
            call_count += 1
            if call_count == 2:
                second_cycle.set()
            return []

        async def controlled_sleep(delay):
            self.assertEqual(delay, 2)
            if not first_cycle_sleep.is_set():
                first_cycle_sleep.set()
                await release_first_cycle.wait()
            else:
                await agent._stop_event.wait()

        self.services["marketplace"].get_available_tasks.side_effect = get_tasks
        with patch("runtime.runtime.asyncio.sleep", new=controlled_sleep):
            await agent.initialize()
            await agent.activate()
            await first_cycle_sleep.wait()

            await agent.pause()
            release_first_cycle.set()
            await real_sleep(0)
            await real_sleep(0)
            self.assertEqual(call_count, 1)
            self.assertEqual(agent.state, AgentState.PAUSED)

            await agent.resume()
            await asyncio.wait_for(second_cycle.wait(), timeout=1)
            await agent.shutdown()

        self.assertEqual(call_count, 2)
        self.assertEqual(agent.state, AgentState.INACTIVE)
        self.assertIsNone(agent._decision_task)

    async def test_pause_waits_for_running_cycle_before_changing_state(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )
        cycle_started = asyncio.Event()
        finish_cycle = asyncio.Event()
        real_sleep = asyncio.sleep

        async def get_tasks(agent_id):
            self.assertEqual(agent_id, self.definition.agent_id)
            cycle_started.set()
            await finish_cycle.wait()
            return []

        self.services["marketplace"].get_available_tasks.side_effect = get_tasks
        with patch(
            "runtime.runtime.asyncio.sleep",
            new=lambda _: real_sleep(0),
        ):
            await agent.initialize()
            await agent.activate()
            await cycle_started.wait()

            pause_task = asyncio.create_task(agent.pause())
            await real_sleep(0)
            self.assertFalse(pause_task.done())
            self.assertEqual(agent.state, AgentState.ACTIVE)

            finish_cycle.set()
            await pause_task

            self.assertEqual(agent.state, AgentState.PAUSED)
            await agent.shutdown()

        self.assertIsNone(agent._decision_task)

    async def test_shutdown_waits_for_running_cycle_and_joins_worker(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )
        cycle_started = asyncio.Event()
        finish_cycle = asyncio.Event()
        real_sleep = asyncio.sleep

        async def get_tasks(agent_id):
            self.assertEqual(agent_id, self.definition.agent_id)
            cycle_started.set()
            await finish_cycle.wait()
            return []

        self.services["marketplace"].get_available_tasks.side_effect = get_tasks
        with patch(
            "runtime.runtime.asyncio.sleep",
            new=lambda _: real_sleep(0),
        ):
            await agent.initialize()
            await agent.activate()
            await cycle_started.wait()
            background_task = agent._decision_task

            shutdown_task = asyncio.create_task(agent.shutdown())
            await real_sleep(0)
            self.assertFalse(shutdown_task.done())
            self.assertEqual(agent.state, AgentState.STOPPING)

            finish_cycle.set()
            await shutdown_task

        self.assertEqual(agent.state, AgentState.INACTIVE)
        self.assertIsNone(agent._decision_task)
        self.assertTrue(background_task.done())

    async def test_repeated_lifecycle_calls_are_rejected_without_corrupting_state(self):
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )
        real_sleep = asyncio.sleep
        self.services["marketplace"].get_available_tasks.return_value = []

        with patch(
            "runtime.runtime.asyncio.sleep",
            new=lambda _: real_sleep(0),
        ):
            await agent.initialize()
            await agent.activate()

            await agent.pause()
            with self.assertRaises(RuntimeError):
                await agent.pause()
            self.assertEqual(agent.state, AgentState.PAUSED)

            await agent.resume()
            with self.assertRaises(RuntimeError):
                await agent.resume()
            self.assertEqual(agent.state, AgentState.ACTIVE)

            await agent.shutdown()
            with self.assertRaises(RuntimeError):
                await agent.shutdown()

        self.assertEqual(agent.state, AgentState.INACTIVE)
        self.assertIsNone(agent._decision_task)

    async def test_decision_cycle_routes_tasks_decisions_bids_and_events(self):
        task = SimpleNamespace(task_id="task-1")
        decision = Mock()
        decision.task_id = "task-1"
        decision.suitable = True
        decision.model_dump.return_value = {
            "task_id": "task-1",
            "suitable": True,
            "match_score": 0.9,
        }
        tasks = [task]
        self.services["marketplace"].get_available_tasks.return_value = tasks
        self.services["decision_engine"].evaluate_tasks.return_value = [
            decision
        ]
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        async def stop_after_cycle(_):
            agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=stop_after_cycle):
            await agent._decision_cycle()

        self.services["marketplace"].get_available_tasks.assert_awaited_once_with(
            self.definition.agent_id
        )
        self.services["decision_engine"].evaluate_tasks.assert_awaited_once_with(
            tasks
        )
        self.services["bidding_engine"].submit_decision.assert_awaited_once_with(
            decision
        )
        events = [
            call.args[0]
            for call in self.services["history_writer"].write_event.await_args_list
        ]
        self.assertEqual(
            [event["event_type"] for event in events],
            [
                "decision_evaluated",
                "bid_submission_completed",
                "decision_cycle_completed",
            ],
        )
        self.assertEqual(events[-1]["tasks_found"], 1)
        self.assertEqual(events[-1]["decisions_made"], 1)
        self.assertEqual(events[-1]["bids_submitted"], 1)

    async def test_decision_cycle_handles_empty_task_list_without_deciding(self):
        self.services["marketplace"].get_available_tasks.return_value = []
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        async def stop_after_cycle(_):
            agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=stop_after_cycle):
            await agent._decision_cycle()

        self.services["decision_engine"].evaluate_tasks.assert_not_awaited()
        self.services["bidding_engine"].submit_decision.assert_not_awaited()
        self.services["history_writer"].write_event.assert_awaited_once_with(
            {
                "event_type": "decision_cycle_completed",
                "agent_id": self.definition.agent_id,
                "tasks_found": 0,
                "decisions_made": 0,
                "bids_submitted": 0,
            }
        )

    async def test_decision_cycle_surfaces_service_failure_and_enters_error(self):
        self.services["marketplace"].get_available_tasks.side_effect = (
            RuntimeError("marketplace unavailable")
        )
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        with self.assertRaisesRegex(RuntimeError, "marketplace unavailable"):
            await agent._decision_cycle()

        self.assertEqual(agent.state, AgentState.ERROR)
        self.services["decision_engine"].evaluate_tasks.assert_not_awaited()
        self.services["bidding_engine"].submit_decision.assert_not_awaited()

    async def test_retryable_error_skips_cycle_and_keeps_agent_active(self):
        from decision_engine.errors import DecisionModelRateLimitError

        self.services["marketplace"].get_available_tasks.return_value = [
            SimpleNamespace(task_id="task-1")
        ]
        self.services["decision_engine"].evaluate_tasks.side_effect = [
            DecisionModelRateLimitError("429"),
            DecisionModelRateLimitError("429"),
            [],
        ]
        agent = MarketAgent(self.definition, self.registry, **self.services)
        agent.state = AgentState.ACTIVE
        sleeps = []

        async def record_sleep(seconds):
            sleeps.append(seconds)
            # Stop after the third (successful) cycle's normal 2s wait.
            if self.services["decision_engine"].evaluate_tasks.await_count == 3:
                agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=record_sleep):
            await agent._decision_cycle()

        self.assertEqual(agent.state, AgentState.ACTIVE)
        self.assertEqual(self.services["decision_engine"].evaluate_tasks.await_count, 3)
        # Backoff 2s (2 x 1s steps), then 4s (4 x 1s steps), then normal 2s wait.
        self.assertEqual(sleeps, [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2])
        self.assertIsNone(agent._retry_delay, "backoff resets after success")
        events = [
            call.args[0]
            for call in self.services["history_writer"].write_event.await_args_list
        ]
        skipped = [e for e in events if e["event_type"] == "decision_cycle_skipped"]
        self.assertEqual([e["retry_in_seconds"] for e in skipped], [2.0, 4.0])
        self.assertEqual(skipped[0]["error_type"], "DecisionModelRateLimitError")
        # ERROR was never written to the registry during the whole run.
        self.assertNotIn(AgentState.ERROR, self.persisted_states)

    async def test_non_retryable_engine_error_still_enters_error(self):
        from decision_engine.errors import DecisionModelInvocationError

        self.services["marketplace"].get_available_tasks.return_value = [
            SimpleNamespace(task_id="task-1")
        ]
        self.services["decision_engine"].evaluate_tasks.side_effect = (
            DecisionModelInvocationError("bad key", retryable=False)
        )
        agent = MarketAgent(self.definition, self.registry, **self.services)

        with self.assertRaises(DecisionModelInvocationError):
            await agent._decision_cycle()

        self.assertEqual(agent.state, AgentState.ERROR)

    async def test_backoff_is_capped_and_shutdown_interrupts_it(self):
        agent = MarketAgent(self.definition, self.registry, **self.services)
        delays = [agent._next_retry_delay() for _ in range(7)]
        self.assertEqual(delays, [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0])

        steps = []

        async def record_sleep(seconds):
            steps.append(seconds)
            agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=record_sleep):
            await agent._sleep_unless_stopped(30.0)
        self.assertEqual(steps, [1.0], "a stop request ends the backoff early")

    async def test_successful_readiness_writes_initialization_event(self):
        self.definition.capability_manifest = ["text_analysis"]
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        await agent.initialize()

        self.assertEqual(agent.state, AgentState.INACTIVE)
        self.services["capability_registry"].is_capability_available.assert_awaited_once_with(
            "text_analysis"
        )
        self.services["history_writer"].write_event.assert_awaited_once_with(
            {
                "event_type": "initialization_complete",
                "agent_id": self.definition.agent_id,
                "state": AgentState.INACTIVE.value,
            }
        )

    def test_capability_manifest_is_serialized_as_machine_readable_data(self):
        self.definition.capability_manifest = ["text_analysis"]

        loaded_definition = AgentDefinition.model_validate_json(
            self.definition.model_dump_json()
        )

        self.assertEqual(
            loaded_definition.capability_manifest,
            ["text_analysis"],
        )

    async def test_registered_text_analysis_capability_is_available_and_invokable(self):
        capability_registry = InMemoryCapabilityRegistry()
        capability_registry.register("text_analysis", analyze_text)

        self.assertTrue(
            await capability_registry.is_capability_available("text_analysis")
        )
        self.assertEqual(
            await capability_registry.invoke_capability(
                "text_analysis",
                "safe local analysis",
            ),
            {"character_count": 19, "word_count": 3},
        )

    async def test_readiness_verifies_declared_capability_handler(self):
        self.definition.capability_manifest = ["text_analysis"]
        capability_registry = InMemoryCapabilityRegistry()
        capability_registry.register("text_analysis", analyze_text)
        services = {
            **self.services,
            "capability_registry": capability_registry,
        }
        agent = MarketAgent(
            self.definition,
            self.registry,
            **services,
        )

        await agent.initialize()

        self.assertEqual(agent.state, AgentState.INACTIVE)

    async def test_readiness_fails_when_declared_capability_has_no_handler(self):
        self.definition.capabilities = ["text_analysis"]
        self.definition.capability_manifest = ["text_analysis"]
        capability_registry = InMemoryCapabilityRegistry()
        services = {
            **self.services,
            "capability_registry": capability_registry,
        }
        agent = MarketAgent(
            self.definition,
            self.registry,
            **services,
        )

        with self.assertRaisesRegex(RuntimeError, "text_analysis"):
            await agent.initialize()

        self.assertEqual(agent.state, AgentState.ERROR)

    async def test_missing_or_unready_dependency_enters_error(self):
        cases = (
            ("unready decision engine", "decision_engine", False),
            ("missing marketplace", "marketplace", None),
            ("missing bidding engine", "bidding_engine", None),
            ("missing history writer", "history_writer", None),
            ("missing dispatcher", "execution_dispatcher", None),
            ("missing capability registry", "capability_registry", None),
        )

        for label, dependency, value in cases:
            with self.subTest(label=label):
                services = _mock_services()
                if dependency == "decision_engine":
                    services[dependency].ready = value
                else:
                    services[dependency] = value
                registry = AsyncMock(spec=AgentRegistry)
                agent = MarketAgent(
                    AgentDefinition(
                        agent_id="test-agent",
                        name="Test Agent",
                    ),
                    registry,
                    **services,
                )

                with self.assertRaises(RuntimeError):
                    await agent.initialize()

                self.assertEqual(agent.state, AgentState.ERROR)

    async def test_unavailable_capability_enters_error(self):
        self.definition.capability_manifest = ["unhandled"]
        self.services["capability_registry"].is_capability_available.return_value = False
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        with self.assertRaisesRegex(RuntimeError, "unhandled"):
            await agent.initialize()

        self.assertEqual(agent.state, AgentState.ERROR)
        self.services["history_writer"].write_event.assert_not_awaited()

    async def test_initialization_event_failure_enters_error(self):
        self.services["history_writer"].write_event.side_effect = OSError(
            "event store unavailable"
        )
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        with self.assertRaisesRegex(OSError, "event store unavailable"):
            await agent.initialize()

        self.assertEqual(agent.state, AgentState.ERROR)

    async def test_invalid_agent_definition_enters_error(self):
        self.definition.agent_id = ""
        agent = MarketAgent(
            self.definition,
            self.registry,
            **self.services,
        )

        with self.assertRaises(ValueError):
            await agent.initialize()

        self.assertEqual(agent.state, AgentState.ERROR)
        self.services["history_writer"].write_event.assert_not_awaited()

    async def test_runtime_passes_injected_services_to_loaded_agent(self):
        self.registry.get.return_value = self.definition
        runtime_services = _runtime_services(self.services)
        runtime = AgentRuntime(
            self.registry,
            **runtime_services,
        )

        agent = await runtime.load_agent(self.definition.agent_id)

        factory = runtime_services["decision_engine_factory"]
        factory.create.assert_called_once_with(self.definition)
        self.assertIs(agent.registry, self.registry)
        self.assertIs(agent.marketplace, self.services["marketplace"])
        self.assertIs(agent.decision_engine, self.services["decision_engine"])
        self.assertIs(agent.bidding_engine, self.services["bidding_engine"])
        self.assertIs(
            agent.capability_registry,
            self.services["capability_registry"],
        )
        self.assertIs(agent.history_writer, self.services["history_writer"])
        self.assertIs(
            agent.execution_dispatcher,
            self.services["execution_dispatcher"],
        )

    async def test_runtime_builds_a_separate_engine_for_each_agent(self):
        definitions = {
            "agent-a": AgentDefinition(agent_id="agent-a", name="A"),
            "agent-b": AgentDefinition(agent_id="agent-b", name="B"),
        }
        self.registry.get.side_effect = lambda agent_id: definitions[agent_id]
        runtime_services = _runtime_services(self.services)
        factory = runtime_services["decision_engine_factory"]
        factory.create.side_effect = lambda definition: Mock(
            spec=DecisionEngineService
        )
        runtime = AgentRuntime(self.registry, **runtime_services)

        agent_a = await runtime.load_agent("agent-a")
        agent_b = await runtime.load_agent("agent-b")
        agent_a_again = await runtime.load_agent("agent-a")

        self.assertIsNot(agent_a.decision_engine, agent_b.decision_engine)
        self.assertIs(agent_a_again, agent_a)
        self.assertEqual(factory.create.call_count, 2)
