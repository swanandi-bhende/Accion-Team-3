"""The agent remembers its decisions across cycles (live-loop safety).

Final outcomes are never re-judged for an unchanged task; temporary ones are
retried; unchanged repeats are not reported or recorded again; a changed
description means the task is judged again.
"""

import unittest

from decision_engine.context.agent_context import AgentDecisionContext
from decision_engine.engine import DecisionEngine
from decision_engine.models.decision import DecisionBatch, TaskDecision
from decision_engine.models.task import MarketplaceTask
from tests.fake_models import FakeStructuredChatModel

NOW = 1_800_000_000.0


def _task(task_id, caps=("analyze_text",), description="Count words.", **kwargs):
    return MarketplaceTask(
        task_id=task_id,
        description=description,
        required_capabilities=list(caps),
        bounty_amount=100.0,
        **kwargs,
    )


def _engine(respond, active=0, max_tasks=3, counter=None):
    model = FakeStructuredChatModel(respond)
    context = AgentDecisionContext(
        agent_id="agent-1",
        capabilities=["analyze_text"],
        max_concurrent_tasks=max_tasks,
        active_task_count=active,
    )
    engine = DecisionEngine(
        context, model=model, clock=lambda: NOW, active_task_counter=counter
    )
    return engine, model


def _approve(*task_ids, score=0.9):
    return lambda _p: DecisionBatch(decisions=[
        TaskDecision(task_id=t, match_score=score, suitable=True, reasoning="fits")
        for t in task_ids
    ])


class DecisionMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_suitable_task_is_not_judged_or_reported_twice(self):
        engine, model = _engine(_approve("t1"))

        first = await engine.evaluate_tasks([_task("t1")])
        second = await engine.evaluate_tasks([_task("t1")])

        self.assertEqual([r.task_id for r in first], ["t1"])
        self.assertEqual(second, [], "nothing new -> nothing sent to bidding again")
        self.assertEqual(model.call_count, 1, "no second LLM call")
        self.assertEqual(len(engine.history.recent()), 1)

    async def test_final_rejections_are_not_rechecked(self):
        engine, model = _engine(_approve("low", score=0.3))
        tasks = [_task("low"), _task("nocap", caps=["image_generation"])]

        first = await engine.evaluate_tasks(tasks)
        second = await engine.evaluate_tasks(tasks)

        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])
        self.assertEqual(model.call_count, 1)

    async def test_new_task_next_cycle_is_judged_alone(self):
        engine, model = _engine(_approve("t1", "t2"))
        await engine.evaluate_tasks([_task("t1")])

        results = await engine.evaluate_tasks([_task("t1"), _task("t2")])

        self.assertEqual([r.task_id for r in results], ["t2"])
        # t1 may appear in the history section, but not in the task list.
        task_list = model.last_prompt_text().split("Available marketplace tasks:")[1]
        task_list = task_list.split("Market context:")[0]
        self.assertNotIn('"task_id": "t1"', task_list)
        self.assertIn('"task_id": "t2"', task_list)

    async def test_changed_description_is_judged_again(self):
        engine, model = _engine(_approve("t1"))
        await engine.evaluate_tasks([_task("t1", description="Count words.")])

        results = await engine.evaluate_tasks(
            [_task("t1", description="Count words and translate to Hindi.")]
        )

        self.assertEqual([r.task_id for r in results], ["t1"])
        self.assertEqual(model.call_count, 2)

    async def test_capacity_full_is_retried_but_not_repeated(self):
        slots = {"active": 3}

        async def counter(_agent_id):
            return slots["active"]

        engine, model = _engine(_approve("t1"), counter=counter)

        first = await engine.evaluate_tasks([_task("t1")])
        second = await engine.evaluate_tasks([_task("t1")])
        self.assertEqual(first[0].rejection_reasons, ["capacity_full"])
        self.assertEqual(second, [], "unchanged temporary outcome is not repeated")
        self.assertEqual(len(engine.history.recent()), 1)

        slots["active"] = 0  # a slot frees up
        third = await engine.evaluate_tasks([_task("t1")])
        self.assertTrue(third[0].suitable, "temporary rejection was retried")
        self.assertEqual(model.call_count, 1)

    async def test_missing_model_decision_is_retried(self):
        replies = iter([DecisionBatch(decisions=[]), _approve("t1")(None)])
        engine, model = _engine(lambda _p: next(replies))

        first = await engine.evaluate_tasks([_task("t1")])
        second = await engine.evaluate_tasks([_task("t1")])

        self.assertEqual(first[0].rejection_reasons, ["missing_model_decision"])
        self.assertTrue(second[0].suitable)
        self.assertEqual(model.call_count, 2)

    async def test_engine_counts_its_private_llm_calls(self):
        engine, _ = _engine(_approve("t1", "t2"))
        await engine.evaluate_tasks([_task("t1")])
        await engine.evaluate_tasks([_task("t1"), _task("t2")])
        self.assertEqual(engine.model_calls, 2)


if __name__ == "__main__":
    unittest.main()
