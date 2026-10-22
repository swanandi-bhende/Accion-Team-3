"""Unit tests for DecisionEngine behaviour, using a labelled fake model."""

import unittest

from decision_engine.context.agent_context import AgentDecisionContext
from decision_engine.engine import DecisionEngine
from decision_engine.models.decision import (
    DecisionBatch,
    DecisionResult,
    TaskDecision,
)
from decision_engine.models.task import MarketplaceTask
from decision_engine.reasoning.prompts import DECISION_PROMPT_VERSION, NO_HISTORY_MARKER
from tests.fake_models import FakeStructuredChatModel


def _task(task_id="t1", caps=("analyze_text",), bounty=100.0, **kwargs):
    return MarketplaceTask(
        task_id=task_id,
        description="Summarise the supplied text",
        required_capabilities=list(caps),
        bounty_amount=bounty,
        **kwargs,
    )


def _context(caps=("analyze_text",), **kwargs):
    return AgentDecisionContext(agent_id="agent-1", capabilities=list(caps), **kwargs)


class NoBidInDecisionEngineTests(unittest.IsolatedAsyncioTestCase):
    """Task 3.2: the Decision Engine decides suitability, never price."""

    async def test_results_and_schemas_carry_no_bid_fields(self):
        model = FakeStructuredChatModel(
            lambda _p: DecisionBatch(
                decisions=[
                    TaskDecision(task_id="t1", match_score=0.9, suitable=True, reasoning="fits")
                ]
            )
        )
        engine = DecisionEngine(_context(), model=model)

        results = await engine.evaluate_tasks([_task()])

        self.assertEqual(len(results), 1)
        dumped = results[0].model_dump()
        for field in ("recommended_bid", "bid_ratio", "bid", "price"):
            self.assertNotIn(field, dumped)
        for schema in (TaskDecision, DecisionResult):
            self.assertNotIn("bid_ratio", schema.model_fields)
            self.assertNotIn("recommended_bid", schema.model_fields)

    async def test_prompt_does_not_ask_the_model_for_a_bid(self):
        model = FakeStructuredChatModel(lambda _p: DecisionBatch(decisions=[]))
        engine = DecisionEngine(_context(), model=model)

        await engine.evaluate_tasks([_task()])

        prompt = model.last_prompt_text().lower()
        self.assertNotIn("bid ratio when suitable", prompt)
        self.assertIn("bidding engine", prompt)


class EmptyHistoryTests(unittest.IsolatedAsyncioTestCase):
    """Task 3.4: history starts genuinely empty; only real decisions appear."""

    def _model(self, score=0.9):
        return FakeStructuredChatModel(
            lambda _p: DecisionBatch(
                decisions=[
                    TaskDecision(
                        task_id="t1", match_score=score, suitable=True, reasoning="fits"
                    )
                ]
            )
        )

    async def test_first_prompt_says_no_history_and_has_no_metrics(self):
        model = self._model()
        engine = DecisionEngine(_context(), model=model)
        self.assertEqual(engine.history.recent(), [])

        await engine.evaluate_tasks([_task()])

        prompt = model.last_prompt_text()
        self.assertIn(NO_HISTORY_MARKER, prompt)
        # No metric data fields (JSON keys) may reach the model.
        for invented in ('"performance"', '"success_rate"', '"win_rate"'):
            self.assertNotIn(invented, prompt)

    async def test_exactly_the_observed_decision_is_retrievable(self):
        model = self._model()
        engine = DecisionEngine(_context(), model=model)

        await engine.evaluate_tasks([_task()])

        records = engine.history.recent()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].task_id, "t1")
        self.assertTrue(records[0].suitable)
        self.assertEqual(records[0].metadata["prompt_version"], DECISION_PROMPT_VERSION)
        self.assertIn("model", records[0].metadata)

    async def test_second_prompt_contains_only_real_history(self):
        model = self._model()
        engine = DecisionEngine(_context(), model=model)

        await engine.evaluate_tasks([_task()])
        await engine.evaluate_tasks([_task("t2")])

        prompt = model.last_prompt_text()
        self.assertNotIn(NO_HISTORY_MARKER, prompt)
        self.assertIn('"task_id": "t1"', prompt)
        # Internal metadata is never shown to the model.
        self.assertNotIn("prompt_version", prompt)

    def test_agent_context_has_no_performance_or_configuration(self):
        fields = AgentDecisionContext.model_fields
        self.assertNotIn("performance", fields)
        self.assertNotIn("configuration", fields)


if __name__ == "__main__":
    unittest.main()
