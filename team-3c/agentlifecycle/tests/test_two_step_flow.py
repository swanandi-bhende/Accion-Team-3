"""The full two-step flow inside each agent's Decision Engine.

Step 1 (shared, once per task): read the description -> capability IDs.
Code: does this agent have every one?
Step 2 (private, per agent): suitability judgement, only for tasks that passed.
"""

import unittest

from agents.models import AgentDefinition
from decision_engine.factory import DecisionEngineFactory
from decision_engine.models.decision import DecisionBatch, TaskDecision
from decision_engine.models.task import MarketplaceTask
from decision_engine.task_reading.catalogue import CapabilityCatalogue
from decision_engine.task_reading.models import (
    TaskRequirementsAnswer,
    TaskRequirementsBatch,
)
from tests.fake_models import FakeStructuredChatModel

NOW = 1_800_000_000.0
CATALOGUE = CapabilityCatalogue(
    {
        "analyze_text": "Word and character statistics for plain text.",
        "translate_text": "Translate plain text between languages.",
    }
)

# What the fake shared reader "reads" from each description.
READINGS = {
    "text": (["analyze_text"], []),
    "translate": (["analyze_text", "translate_text"], []),
    "video": ([], ["video editing"]),
}


def _task(task_id, **kwargs):
    kwargs.setdefault("description", f"Description of the {task_id} task.")
    return MarketplaceTask(task_id=task_id, bounty_amount=100.0, **kwargs)


def _reader_reply(prompt):
    text = "\n".join(m.content for m in prompt.to_messages())
    answers = [
        TaskRequirementsAnswer(
            task_id=task_id,
            required_capabilities=required,
            unsupported_requirements=unsupported,
            reasoning="read",
        )
        for task_id, (required, unsupported) in READINGS.items()
        if f'"{task_id}"' in text
    ]
    return TaskRequirementsBatch(tasks=answers)


def _judge_reply(prompt):
    text = "\n".join(m.content for m in prompt.to_messages())
    return DecisionBatch(decisions=[
        TaskDecision(task_id=task_id, match_score=0.9, suitable=True, reasoning="fits")
        for task_id in READINGS
        if f'"task_id": "{task_id}"' in text
    ])


def _setup(*manifests):
    model = FakeStructuredChatModel(
        by_schema={TaskRequirementsBatch: _reader_reply, DecisionBatch: _judge_reply}
    )
    factory = DecisionEngineFactory(model, capability_catalogue=CATALOGUE)
    engines = []
    for index, manifest in enumerate(manifests):
        engine = factory.create(
            AgentDefinition(
                agent_id=f"agent-{index}", name="A", capability_manifest=list(manifest)
            )
        )
        engine._clock = lambda: NOW
        engines.append(engine)
    return model, factory, engines


def _by_id(results):
    return {r.task_id: r for r in results}


class TwoStepFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_description_only_task_is_read_then_judged(self):
        model, _, (engine,) = _setup(["analyze_text"])

        results = _by_id(await engine.evaluate_tasks([_task("text")]))

        r = results["text"]
        self.assertTrue(r.suitable)
        self.assertEqual(r.requirements_source, "extracted")
        self.assertEqual(r.required_capabilities, ["analyze_text"])
        self.assertEqual(model.calls_for(TaskRequirementsBatch), 1)
        self.assertEqual(model.calls_for(DecisionBatch), 1)
        # The private call sees the requirements read in Step 1.
        self.assertIn('"required_capabilities": ["analyze_text"]',
                      model.last_prompt_text(DecisionBatch))

    async def test_missing_capability_is_rejected_before_private_call(self):
        model, _, (engine,) = _setup(["analyze_text"])

        results = _by_id(await engine.evaluate_tasks([_task("translate")]))

        r = results["translate"]
        self.assertFalse(r.suitable)
        self.assertEqual(r.rejection_reasons, ["capability_missing"])
        self.assertEqual(r.capability_gap, ["translate_text"])
        self.assertEqual(r.requirements_source, "extracted")
        self.assertEqual(model.calls_for(DecisionBatch), 0)

    async def test_unsupported_need_can_never_be_met(self):
        model, _, (engine,) = _setup(["analyze_text", "translate_text"])
        r = (await engine.evaluate_tasks([_task("video")]))[0]
        self.assertEqual(r.rejection_reasons, ["capability_missing"])
        self.assertEqual(r.capability_gap, ["video editing"])
        self.assertEqual(model.calls_for(DecisionBatch), 0)

    async def test_two_agents_share_one_reading_but_judge_privately(self):
        model, factory, (a, b) = _setup(["analyze_text"], ["analyze_text", "translate_text"])
        tasks = [_task("text"), _task("translate")]

        results_a = _by_id(await a.evaluate_tasks(tasks))
        results_b = _by_id(await b.evaluate_tasks(tasks))

        # Read once, judged once per agent.
        self.assertEqual(model.calls_for(TaskRequirementsBatch), 1)
        self.assertEqual(model.calls_for(DecisionBatch), 2)
        self.assertIs(a.task_reader, b.task_reader)
        # Same reading, different agents, different outcomes.
        self.assertFalse(results_a["translate"].suitable)
        self.assertTrue(results_b["translate"].suitable)
        self.assertTrue(results_a["text"].suitable)
        # Histories stay private.
        self.assertIsNot(a.history, b.history)

    async def test_agent_without_capabilities_triggers_no_llm_call(self):
        model, _, (engine,) = _setup([])
        results = await engine.evaluate_tasks([_task("text"), _task("translate")])
        self.assertTrue(all(r.rejection_reasons == ["capability_missing"] for r in results))
        self.assertEqual(model.call_count, 0)

    async def test_closed_and_expired_tasks_are_not_read(self):
        model, _, (engine,) = _setup(["analyze_text"])
        results = _by_id(await engine.evaluate_tasks([
            _task("text", status="completed"),
            _task("translate", deadline=int(NOW) - 1),
        ]))
        self.assertEqual(results["text"].rejection_reasons, ["task_not_open"])
        self.assertEqual(results["translate"].rejection_reasons, ["deadline_passed"])
        self.assertEqual(model.call_count, 0)

    async def test_unreadable_task_is_rejected_without_private_call(self):
        model, _, (engine,) = _setup(["analyze_text"])
        # "mystery" is not in READINGS, so the fake reader gives no answer.
        r = (await engine.evaluate_tasks([_task("mystery")]))[0]
        self.assertEqual(r.rejection_reasons, ["requirements_unreadable"])
        self.assertEqual(r.requirements_source, "unreadable")
        self.assertEqual(model.calls_for(DecisionBatch), 0)

    async def test_declared_requirements_still_work_without_reading(self):
        model, _, (engine,) = _setup(["analyze_text"])
        r = (await engine.evaluate_tasks([
            _task("text", required_capabilities=["analyze_text"])
        ]))[0]
        self.assertTrue(r.suitable)
        self.assertEqual(r.requirements_source, "declared")
        self.assertEqual(model.calls_for(TaskRequirementsBatch), 0)

    async def test_history_records_where_requirements_came_from(self):
        _, _, (engine,) = _setup(["analyze_text"])
        await engine.evaluate_tasks([_task("text")])
        record = engine.history.recent()[0]
        self.assertEqual(record.metadata["requirements_source"], "extracted")

    def test_without_catalogue_there_is_no_reader(self):
        model = FakeStructuredChatModel(lambda _p: DecisionBatch())
        factory = DecisionEngineFactory(model)
        self.assertIsNone(factory.task_reader)


if __name__ == "__main__":
    unittest.main()
