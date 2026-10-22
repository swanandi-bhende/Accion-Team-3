"""Step 1: the shared Task Reader (description -> catalogue capability IDs)."""

import asyncio
import unittest

from decision_engine.errors import DecisionModelInvocationError
from decision_engine.models.task import MarketplaceTask
from decision_engine.task_reading.catalogue import CapabilityCatalogue
from decision_engine.task_reading.models import (
    RequirementsSource,
    TaskRequirementsAnswer,
    TaskRequirementsBatch,
)
from decision_engine.task_reading.reader import TaskReader
from tests.fake_models import FakeStructuredChatModel

CATALOGUE = CapabilityCatalogue(
    {
        "analyze_text": "Word and character statistics for plain text.",
        "translate_text": "Translate plain text between languages.",
    }
)


def _task(task_id, description="Count the words in this paragraph.", **kwargs):
    return MarketplaceTask(
        task_id=task_id, description=description, bounty_amount=100.0, **kwargs
    )


def _answer(task_id, required=("analyze_text",), unsupported=()):
    return TaskRequirementsAnswer(
        task_id=task_id,
        required_capabilities=list(required),
        unsupported_requirements=list(unsupported),
        reasoning="r",
    )


def _reader(answers=None, respond=None):
    if respond is None:
        respond = lambda _p: TaskRequirementsBatch(tasks=list(answers or []))
    model = FakeStructuredChatModel(respond)
    return TaskReader(model, CATALOGUE), model


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_catalogue_ids(self):
        reader, _ = _reader([_answer("t1", ["analyze_text", "translate_text"])])
        reading = (await reader.read([_task("t1")]))["t1"]
        self.assertEqual(reading.source, RequirementsSource.EXTRACTED)
        self.assertEqual(reading.required_capabilities, ["analyze_text", "translate_text"])
        self.assertEqual(reading.unsupported_requirements, [])

    async def test_ids_outside_catalogue_become_unsupported(self):
        # The model invents "image_generation": code must not trust it as an ID.
        reader, _ = _reader([
            _answer("t1", ["analyze_text", "image_generation"], ["video editing"])
        ])
        reading = (await reader.read([_task("t1")]))["t1"]
        self.assertEqual(reading.required_capabilities, ["analyze_text"])
        self.assertEqual(
            reading.unsupported_requirements, ["video editing", "image_generation"]
        )

    async def test_nothing_identified_means_unspecified(self):
        reader, _ = _reader([_answer("t1", [], [])])
        reading = (await reader.read([_task("t1", "Do the thing.")]))["t1"]
        self.assertEqual(reading.source, RequirementsSource.UNSPECIFIED)

    async def test_declared_requirements_skip_the_model(self):
        reader, model = _reader([])
        reading = (await reader.read([
            _task("t1", required_capabilities=["analyze_text"])
        ]))["t1"]
        self.assertEqual(reading.source, RequirementsSource.DECLARED)
        self.assertEqual(model.call_count, 0)

    async def test_no_description_and_nothing_declared_is_unspecified(self):
        reader, model = _reader([])
        reading = (await reader.read([_task("t1", description=None)]))["t1"]
        self.assertEqual(reading.source, RequirementsSource.UNSPECIFIED)
        self.assertEqual(model.call_count, 0)

    async def test_reader_sees_only_id_and_description(self):
        reader, model = _reader([_answer("t1")])
        await reader.read([_task("t1", creator="0xCREATOR", market_context={"x": 1})])
        prompt = model.last_prompt_text()
        self.assertIn("Count the words", prompt)
        self.assertIn("analyze_text", prompt)  # catalogue is shown
        for hidden in ("0xCREATOR", "bounty_amount", "100.0"):
            self.assertNotIn(hidden, prompt)


class AnswerValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_ids_ignored_missing_and_duplicates_unreadable(self):
        reader, _ = _reader([
            _answer("t1"),
            _answer("t2"), _answer("t2"),
            _answer("invented"),
        ])
        with self.assertLogs("team3c.decision_engine", level="WARNING") as logs:
            readings = await reader.read([_task("t1"), _task("t2"), _task("t3")])
        self.assertIn("invented", "\n".join(logs.output))
        self.assertEqual(set(readings), {"t1", "t2", "t3"})
        self.assertEqual(readings["t1"].source, RequirementsSource.EXTRACTED)
        self.assertEqual(readings["t2"].source, RequirementsSource.UNREADABLE)
        self.assertEqual(readings["t3"].source, RequirementsSource.UNREADABLE)

    async def test_unreadable_is_not_cached_so_next_cycle_retries(self):
        replies = iter([
            TaskRequirementsBatch(tasks=[]),           # first cycle: no answer
            TaskRequirementsBatch(tasks=[_answer("t1")]),  # second cycle: answered
        ])
        reader, model = _reader(respond=lambda _p: next(replies))
        first = (await reader.read([_task("t1")]))["t1"]
        second = (await reader.read([_task("t1")]))["t1"]
        self.assertEqual(first.source, RequirementsSource.UNREADABLE)
        self.assertEqual(second.source, RequirementsSource.EXTRACTED)
        self.assertEqual(model.call_count, 2)

    async def test_provider_failure_raises_typed_error(self):
        def fail(_p):
            raise ConnectionError("network down")

        reader, _ = _reader(respond=fail)
        with self.assertRaises(DecisionModelInvocationError):
            await reader.read([_task("t1")])


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_task_is_read_once(self):
        reader, model = _reader([_answer("t1"), _answer("t2")])
        await reader.read([_task("t1"), _task("t2")])
        await reader.read([_task("t1"), _task("t2")])
        self.assertEqual(model.call_count, 1)

    async def test_only_new_tasks_are_sent(self):
        def respond(prompt):
            text = "\n".join(m.content for m in prompt.to_messages())
            ids = [tid for tid in ("t1", "t2") if f'"{tid}"' in text]
            return TaskRequirementsBatch(tasks=[_answer(tid) for tid in ids])

        reader, model = _reader(respond=respond)
        await reader.read([_task("t1")])
        await reader.read([_task("t1"), _task("t2")])
        self.assertEqual(model.call_count, 2)
        self.assertNotIn('"t1"', model.last_prompt_text())

    async def test_changed_description_is_read_again(self):
        reader, model = _reader([_answer("t1")])
        await reader.read([_task("t1", "Count the words.")])
        await reader.read([_task("t1", "Count the words AND translate it.")])
        self.assertEqual(model.call_count, 2)

    async def test_concurrent_agents_trigger_one_reading(self):
        # Five agents polling at the same moment must not read t1 five times.
        reader, model = _reader([_answer("t1")])
        await asyncio.gather(*(reader.read([_task("t1")]) for _ in range(5)))
        self.assertEqual(model.call_count, 1)


class CatalogueTests(unittest.TestCase):
    def test_catalogue_requires_ids_and_descriptions(self):
        with self.assertRaises(ValueError):
            CapabilityCatalogue({})
        with self.assertRaises(ValueError):
            CapabilityCatalogue({"analyze_text": ""})
        with self.assertRaises(ValueError):
            CapabilityCatalogue({" ": "x"})

    def test_fingerprint_changes_with_content(self):
        a = CapabilityCatalogue({"x": "one"})
        b = CapabilityCatalogue({"x": "two"})
        self.assertNotEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.fingerprint, CapabilityCatalogue({"x": "one"}).fingerprint)


if __name__ == "__main__":
    unittest.main()
