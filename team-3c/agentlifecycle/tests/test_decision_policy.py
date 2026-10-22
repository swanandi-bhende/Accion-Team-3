"""Task 3.3: deterministic policy around the LLM.

Manual "verify before continuing": high-score but unsupported task, full
concurrency, invalid score, duplicate/unknown IDs, contradictory
suitable/score outputs. Every test uses a labelled fake model.
"""

import asyncio
import unittest

from langchain_core.exceptions import OutputParserException

from decision_engine.context.agent_context import AgentDecisionContext
from decision_engine.engine import DecisionEngine
from decision_engine.errors import (
    DecisionModelInvocationError,
    DecisionModelOutputError,
    DecisionModelTimeoutError,
)
from decision_engine.models.decision import DecisionBatch, TaskDecision
from decision_engine.models.task import MarketplaceTask
from decision_engine.policy.decision_policy import DecisionPolicy
from tests.fake_models import FakeStructuredChatModel

NOW = 1_800_000_000.0


def _task(task_id, caps=("analyze_text",), **kwargs):
    return MarketplaceTask(
        task_id=task_id,
        description="Summarise the supplied text",
        required_capabilities=list(caps),
        bounty_amount=100.0,
        **kwargs,
    )


def _decision(task_id, score=0.9, suitable=True):
    return TaskDecision(
        task_id=task_id, match_score=score, suitable=suitable, reasoning="r"
    )


def _engine(decisions=None, respond=None, caps=("analyze_text",), max_tasks=3,
            active=0, **kwargs):
    if respond is None:
        respond = lambda _p: DecisionBatch(decisions=list(decisions or []))
    model = FakeStructuredChatModel(respond)
    context = AgentDecisionContext(
        agent_id="agent-1",
        capabilities=list(caps),
        max_concurrent_tasks=max_tasks,
        active_task_count=active,
    )
    engine = DecisionEngine(context, model=model, clock=lambda: NOW, **kwargs)
    return engine, model


def _by_id(results):
    return {r.task_id: r for r in results}


class PrecheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_high_score_but_unsupported_task_is_rejected_in_code(self):
        # The model would love it, but the agent lacks the capability.
        engine, model = _engine([_decision("t1", score=0.99)])

        results = await engine.evaluate_tasks([_task("t1", caps=["image_gen"])])

        r = results[0]
        self.assertFalse(r.suitable)
        self.assertEqual(r.rejection_reasons, ["capability_missing"])
        self.assertEqual(r.capability_gap, ["image_gen"])
        self.assertIsNone(r.match_score)
        self.assertEqual(model.call_count, 0, "unsupported task must not reach the LLM")

    async def test_agent_without_capabilities_never_calls_the_model(self):
        engine, model = _engine([], caps=())

        results = await engine.evaluate_tasks([_task("t1"), _task("t2")])

        self.assertEqual(model.call_count, 0)
        self.assertTrue(all(not r.suitable for r in results))
        self.assertTrue(
            all(r.rejection_reasons == ["capability_missing"] for r in results)
        )

    async def test_task_without_declared_requirements_is_rejected(self):
        engine, model = _engine([])
        results = await engine.evaluate_tasks([_task("t1", caps=())])
        self.assertEqual(results[0].rejection_reasons, ["requirements_unspecified"])
        self.assertEqual(model.call_count, 0)

    async def test_closed_or_expired_tasks_are_rejected(self):
        engine, model = _engine([])
        results = _by_id(await engine.evaluate_tasks([
            _task("closed", status="completed"),
            _task("expired", deadline=int(NOW) - 1),
        ]))
        self.assertEqual(results["closed"].rejection_reasons, ["task_not_open"])
        self.assertEqual(results["expired"].rejection_reasons, ["deadline_passed"])
        self.assertEqual(model.call_count, 0)

    async def test_only_candidates_are_sent_to_the_model(self):
        engine, model = _engine([_decision("ok")])
        await engine.evaluate_tasks([_task("ok"), _task("bad", caps=["x"])])
        prompt = model.last_prompt_text()
        self.assertIn('"ok"', prompt)
        self.assertNotIn('"bad"', prompt)

    async def test_empty_task_list_makes_no_decisions(self):
        engine, model = _engine([])
        self.assertEqual(await engine.evaluate_tasks([]), [])
        self.assertEqual(model.call_count, 0)
        self.assertEqual(engine.history.recent(), [])


class ModelOutputValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_threshold_is_inclusive_at_0_7(self):
        engine, _ = _engine([_decision("a", 0.7), _decision("b", 0.69)])
        results = _by_id(await engine.evaluate_tasks([_task("a"), _task("b")]))
        self.assertTrue(results["a"].suitable)
        self.assertEqual(results["b"].rejection_reasons, ["score_below_threshold"])

    async def test_contradictory_outputs_are_rejected(self):
        engine, _ = _engine([
            _decision("yes_low", score=0.3, suitable=True),
            _decision("no_high", score=0.95, suitable=False),
        ])
        results = _by_id(await engine.evaluate_tasks([_task("yes_low"), _task("no_high")]))
        self.assertEqual(results["yes_low"].rejection_reasons, ["score_below_threshold"])
        self.assertEqual(results["no_high"].rejection_reasons, ["model_not_suitable"])
        self.assertFalse(results["no_high"].suitable)
        self.assertFalse(results["no_high"].model_suitable)

    async def test_unknown_task_ids_are_ignored_and_logged(self):
        engine, _ = _engine([_decision("t1"), _decision("invented")])
        with self.assertLogs("team3c.decision_engine", level="WARNING") as logs:
            results = await engine.evaluate_tasks([_task("t1")])
        self.assertEqual([r.task_id for r in results], ["t1"])
        self.assertIn("invented", "\n".join(logs.output))

    async def test_duplicate_model_decisions_reject_the_task(self):
        engine, _ = _engine([_decision("t1", 0.9), _decision("t1", 0.2)])
        results = await engine.evaluate_tasks([_task("t1")])
        self.assertFalse(results[0].suitable)
        self.assertEqual(results[0].rejection_reasons, ["duplicate_task_decision"])

    async def test_missing_model_decision_rejects_the_task(self):
        engine, _ = _engine([_decision("t1")])
        results = _by_id(await engine.evaluate_tasks([_task("t1"), _task("t2")]))
        self.assertTrue(results["t1"].suitable)
        self.assertEqual(results["t2"].rejection_reasons, ["missing_model_decision"])

    async def test_invalid_score_from_model_raises_typed_output_error(self):
        def respond(_p):
            # What with_structured_output raises when the model returns
            # e.g. match_score=1.7.
            TaskDecision(task_id="t1", match_score=1.7, suitable=True, reasoning="r")

        engine, _ = _engine(respond=respond)
        with self.assertRaises(DecisionModelOutputError):
            await engine.evaluate_tasks([_task("t1")])
        self.assertEqual(engine.history.recent(), [], "no decision recorded on failure")

    async def test_parser_failure_raises_typed_output_error(self):
        def respond(_p):
            raise OutputParserException("not json")

        engine, _ = _engine(respond=respond)
        with self.assertRaises(DecisionModelOutputError):
            await engine.evaluate_tasks([_task("t1")])

    async def test_wrong_return_type_raises_typed_output_error(self):
        engine, _ = _engine(respond=lambda _p: {"decisions": []})
        with self.assertRaises(DecisionModelOutputError):
            await engine.evaluate_tasks([_task("t1")])

    async def test_provider_failure_raises_typed_invocation_error(self):
        def respond(_p):
            raise ConnectionError("network down")

        engine, _ = _engine(respond=respond)
        with self.assertRaises(DecisionModelInvocationError):
            await engine.evaluate_tasks([_task("t1")])

    async def test_provider_errors_are_classified_retryable_or_not(self):
        from langchain_core.exceptions import (
            ModelAPIError,
            ModelAuthenticationError,
            ModelRateLimitError,
        )

        from decision_engine.errors import DecisionModelRateLimitError

        cases = [
            (ModelRateLimitError("429"), DecisionModelRateLimitError, True),
            (ModelAPIError("503"), DecisionModelInvocationError, True),
            (ConnectionError("down"), DecisionModelInvocationError, True),
            (ModelAuthenticationError("bad key"), DecisionModelInvocationError, False),
            (ValueError("unexpected"), DecisionModelInvocationError, False),
        ]
        for raised, expected_type, retryable in cases:
            with self.subTest(error=type(raised).__name__):
                def respond(_p, exc=raised):
                    raise exc

                engine, _ = _engine(respond=respond)
                with self.assertRaises(expected_type) as ctx:
                    await engine.evaluate_tasks([_task("t1")])
                self.assertIs(ctx.exception.retryable, retryable)

        engine, _ = _engine(respond=lambda _p: {"not": "a batch"})
        with self.assertRaises(DecisionModelOutputError) as ctx:
            await engine.evaluate_tasks([_task("t1")])
        self.assertFalse(ctx.exception.retryable)

    async def test_slow_model_raises_typed_timeout_error(self):
        model = FakeStructuredChatModel(lambda _p: DecisionBatch())
        context = AgentDecisionContext(agent_id="a", capabilities=["analyze_text"])
        engine = DecisionEngine(
            context, model=model, clock=lambda: NOW, invocation_timeout_seconds=0.05
        )

        async def hang(_payload):
            await asyncio.sleep(5)

        engine.chain = type("SlowChain", (), {"ainvoke": staticmethod(hang)})()
        with self.assertRaises(DecisionModelTimeoutError):
            await engine.evaluate_tasks([_task("t1")])


class CapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_concurrency_rejects_all_without_calling_model(self):
        engine, model = _engine([_decision("t1")], max_tasks=3, active=3)
        results = await engine.evaluate_tasks([_task("t1")])
        self.assertEqual(results[0].rejection_reasons, ["capacity_full"])
        self.assertEqual(model.call_count, 0)

    async def test_only_free_slots_are_filled_best_score_first(self):
        engine, _ = _engine(
            [_decision("a", 0.8), _decision("b", 0.95), _decision("c", 0.9)],
            max_tasks=3,
            active=1,
        )
        results = _by_id(await engine.evaluate_tasks([_task("a"), _task("b"), _task("c")]))
        self.assertTrue(results["b"].suitable)
        self.assertTrue(results["c"].suitable)
        self.assertFalse(results["a"].suitable)
        self.assertEqual(results["a"].rejection_reasons, ["capacity_full"])

    async def test_active_task_counter_overrides_context(self):
        async def counter(agent_id):
            self.assertEqual(agent_id, "agent-1")
            return 3

        engine, model = _engine([_decision("t1")], max_tasks=3, active=0,
                                active_task_counter=counter)
        results = await engine.evaluate_tasks([_task("t1")])
        self.assertEqual(results[0].rejection_reasons, ["capacity_full"])
        self.assertEqual(model.call_count, 0)


class OrderingAndHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_sorted_by_score_and_all_recorded(self):
        engine, _ = _engine([_decision("a", 0.75), _decision("b", 0.95)])
        results = await engine.evaluate_tasks(
            [_task("a"), _task("b"), _task("c", caps=["x"])]
        )
        self.assertEqual([r.task_id for r in results], ["b", "a", "c"])
        recorded = [r.task_id for r in engine.history.recent()]
        self.assertEqual(recorded, ["b", "a", "c"])
        self.assertEqual(
            engine.history.recent()[2].rejection_reasons, ["capability_missing"]
        )

    async def test_duplicate_input_tasks_are_evaluated_once(self):
        engine, _ = _engine([_decision("t1")])
        results = await engine.evaluate_tasks([_task("t1"), _task("t1")])
        self.assertEqual(len(results), 1)


class PolicyUnitTests(unittest.TestCase):
    def test_threshold_must_be_a_valid_probability(self):
        with self.assertRaises(ValueError):
            DecisionPolicy(min_match_score=1.5)

    def test_free_slots_never_negative(self):
        self.assertEqual(DecisionPolicy.free_slots(5, 3), 0)
        self.assertEqual(DecisionPolicy.free_slots(1, 3), 2)


if __name__ == "__main__":
    unittest.main()
