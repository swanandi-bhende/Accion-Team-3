"""WP1 + WP2 together: real runtime, real factory and Decision Engine.

Only the LLM (labelled fake), marketplace, bidding, history writer and
dispatcher are test doubles. Proves the per-agent engine plugs into the
runtime's readiness check and decision cycle, and that only policy-approved
decisions reach the Bidding Engine.
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from agents.models import AgentDefinition, AgentState
from decision_engine.factory import DecisionEngineFactory
from decision_engine.models.decision import DecisionBatch, TaskDecision
from decision_engine.models.task import MarketplaceTask
from interfaces.bid_manager import BidManager
from interfaces.marketplace import TaskMarketplaceService
from interfaces.runtime_services import ExecutionDispatcher, HistoryEventWriter
from registry.interface import AgentRegistry
from runtime.capabilities import analyze_text
from runtime.capability_registry import InMemoryCapabilityRegistry
from runtime.runtime import AgentRuntime
from tests.fake_models import FakeStructuredChatModel


def _task(task_id, caps):
    return MarketplaceTask(
        task_id=task_id,
        description="demo listing",
        required_capabilities=caps,
        bounty_amount=100.0,
        market_context={"environment": "simulation"},
    )


class RuntimeWithDecisionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.definition = AgentDefinition(
            agent_id="demo-agent",
            name="Demo Agent",
            capability_manifest=["analyze_text"],
        )
        self.registry = AsyncMock(spec=AgentRegistry)
        self.registry.get.return_value = self.definition

        capability_registry = InMemoryCapabilityRegistry()
        capability_registry.register("analyze_text", analyze_text)

        self.model = FakeStructuredChatModel(
            lambda _p: DecisionBatch(decisions=[
                TaskDecision(task_id="good", match_score=0.9, suitable=True,
                             reasoning="text task"),
                TaskDecision(task_id="weak", match_score=0.4, suitable=False,
                             reasoning="poor fit"),
            ])
        )
        self.marketplace = Mock(spec=TaskMarketplaceService)
        self.marketplace.get_available_tasks = AsyncMock(return_value=[
            _task("good", ["analyze_text"]),
            _task("weak", ["analyze_text"]),
            _task("image", ["image_generation"]),
        ])
        self.bidding = Mock(spec=BidManager)
        self.bidding.submit_decision = AsyncMock()
        self.history_writer = AsyncMock(spec=HistoryEventWriter)

        self.runtime = AgentRuntime(
            self.registry,
            marketplace=self.marketplace,
            decision_engine_factory=DecisionEngineFactory(self.model),
            bidding_engine=self.bidding,
            capability_registry=capability_registry,
            history_writer=self.history_writer,
            execution_dispatcher=Mock(spec=ExecutionDispatcher),
        )

    async def test_one_cycle_sends_only_approved_tasks_to_bidding(self):
        agent = await self.runtime.load_agent("demo-agent")
        await agent.initialize()
        self.assertEqual(agent.state, AgentState.INACTIVE)

        async def stop_after_cycle(_):
            agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=stop_after_cycle):
            await agent._decision_cycle()

        # Only "good" passed the LLM + every hard rule.
        submitted = [c.args[0].task_id for c in self.bidding.submit_decision.await_args_list]
        self.assertEqual(submitted, ["good"])

        # The unsupported task never reached the model.
        self.assertEqual(self.model.call_count, 1)
        self.assertNotIn('"image"', self.model.last_prompt_text())

        # The agent's private history holds exactly the three real decisions.
        engine = agent.decision_engine
        self.assertEqual(
            sorted(r.task_id for r in engine.history.recent()),
            ["good", "image", "weak"],
        )

    async def test_description_only_tasks_go_through_shared_reading(self):
        """Team 3A tasks carry no capability field: only a description."""
        from decision_engine.task_reading.catalogue import DEMO_CAPABILITY_CATALOGUE
        from decision_engine.task_reading.models import (
            TaskRequirementsAnswer,
            TaskRequirementsBatch,
        )

        def read(_prompt):
            return TaskRequirementsBatch(tasks=[
                TaskRequirementsAnswer(task_id="good", required_capabilities=["analyze_text"],
                                       unsupported_requirements=[], reasoning="text"),
                TaskRequirementsAnswer(task_id="image", required_capabilities=[],
                                       unsupported_requirements=["image generation"],
                                       reasoning="needs images"),
            ])

        def judge(_prompt):
            return DecisionBatch(decisions=[
                TaskDecision(task_id="good", match_score=0.9, suitable=True, reasoning="fits"),
            ])

        model = FakeStructuredChatModel(
            by_schema={TaskRequirementsBatch: read, DecisionBatch: judge}
        )
        self.marketplace.get_available_tasks = AsyncMock(return_value=[
            MarketplaceTask(task_id="good", description="Count the words in this text.",
                            bounty_amount=100.0),
            MarketplaceTask(task_id="image", description="Draw a product photo.",
                            bounty_amount=100.0),
        ])
        self.runtime.decision_engine_factory = DecisionEngineFactory(
            model, capability_catalogue=DEMO_CAPABILITY_CATALOGUE
        )
        agent = await self.runtime.load_agent("demo-agent")
        await agent.initialize()

        async def stop_after_cycle(_):
            agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=stop_after_cycle):
            await agent._decision_cycle()

        submitted = [c.args[0].task_id for c in self.bidding.submit_decision.await_args_list]
        self.assertEqual(submitted, ["good"])
        self.assertEqual(model.calls_for(TaskRequirementsBatch), 1)
        self.assertEqual(model.calls_for(DecisionBatch), 1)
        self.assertNotIn('"image"', model.last_prompt_text(DecisionBatch))

    async def test_live_loop_does_not_resend_or_rejudge(self):
        agent = await self.runtime.load_agent("demo-agent")
        await agent.initialize()
        cycles = {"n": 0}

        async def stop_after_three_cycles(_):
            cycles["n"] += 1
            if cycles["n"] == 3:
                agent._stop_event.set()

        with patch("runtime.runtime.asyncio.sleep", new=stop_after_three_cycles):
            await agent._decision_cycle()

        submitted = [c.args[0].task_id for c in self.bidding.submit_decision.await_args_list]
        self.assertEqual(submitted, ["good"], "bidding receives the task once, not 3 times")
        self.assertEqual(self.model.call_count, 1, "one LLM call across 3 cycles")

    async def test_unconfigured_model_blocks_initialization(self):
        self.runtime.decision_engine_factory = DecisionEngineFactory(None)
        agent = await self.runtime.load_agent("demo-agent")

        with self.assertRaises(RuntimeError):
            await agent.initialize()
        self.assertEqual(agent.state, AgentState.ERROR)


if __name__ == "__main__":
    unittest.main()
