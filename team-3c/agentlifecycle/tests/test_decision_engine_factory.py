"""Unit tests for the per-agent Decision Engine factory."""

import os
import unittest
from unittest.mock import patch

from agents.models import AgentDefinition
from decision_engine.factory import (
    AgentContextError,
    DecisionEngineFactory,
    build_agent_decision_context,
)
from decision_engine.model.configuration import ProviderConfigurationError
from decision_engine.models.decision import DecisionBatch
from tests.fake_models import FakeStructuredChatModel


def _fake_model():
    return FakeStructuredChatModel(lambda _prompt: DecisionBatch(decisions=[]))


class AgentContextTests(unittest.TestCase):
    def test_capabilities_come_only_from_verified_manifest(self):
        definition = AgentDefinition(
            agent_id="a1",
            name="A1",
            capabilities=["unverified_label"],
            capability_manifest=["analyze_text", "analyze_text"],
            performance={"success_rate": 0.99},
        )

        context = build_agent_decision_context(definition)

        self.assertEqual(context.capabilities, ["analyze_text"])
        self.assertNotIn("unverified_label", context.capabilities)

    def test_framework_agent_without_capabilities_has_empty_list(self):
        definition = AgentDefinition(agent_id="a1", name="A1")
        context = build_agent_decision_context(definition)
        self.assertEqual(context.capabilities, [])

    def test_max_concurrent_tasks_is_read_and_validated(self):
        ok = AgentDefinition(
            agent_id="a1", name="A1", configuration={"max_concurrent_tasks": 5}
        )
        self.assertEqual(build_agent_decision_context(ok).max_concurrent_tasks, 5)

        for bad in (0, -1, "3", 2.5, True):
            with self.subTest(value=bad):
                definition = AgentDefinition(
                    agent_id="a1",
                    name="A1",
                    configuration={"max_concurrent_tasks": bad},
                )
                with self.assertRaises(AgentContextError):
                    build_agent_decision_context(definition)


class DecisionEngineFactoryTests(unittest.TestCase):
    def setUp(self):
        self.def_a = AgentDefinition(
            agent_id="a", name="A", capability_manifest=["analyze_text"]
        )
        self.def_b = AgentDefinition(agent_id="b", name="B")

    def test_each_agent_gets_its_own_engine_and_history(self):
        factory = DecisionEngineFactory(_fake_model())

        engine_a = factory.create(self.def_a)
        engine_b = factory.create(self.def_b)

        self.assertIsNot(engine_a, engine_b)
        self.assertIsNot(engine_a.history, engine_b.history)
        self.assertEqual(engine_a.agent_context.agent_id, "a")
        self.assertEqual(engine_b.agent_context.agent_id, "b")

    def test_model_and_policy_are_shared(self):
        model = _fake_model()
        factory = DecisionEngineFactory(model)

        engine_a = factory.create(self.def_a)
        engine_b = factory.create(self.def_b)

        self.assertIs(engine_a.model, model)
        self.assertIs(engine_b.model, model)
        self.assertIs(engine_a.policy, engine_b.policy)

    def test_engines_are_ready_only_when_a_model_is_configured(self):
        self.assertTrue(DecisionEngineFactory(_fake_model()).create(self.def_a).ready)

        unconfigured = DecisionEngineFactory(None)
        self.assertFalse(unconfigured.model_configured)
        self.assertFalse(unconfigured.create(self.def_a).ready)

    def test_new_engine_history_starts_empty(self):
        engine = DecisionEngineFactory(_fake_model()).create(self.def_a)
        self.assertEqual(engine.history.recent(), [])

    def test_from_configuration_fails_clearly_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderConfigurationError):
                DecisionEngineFactory.from_configuration()


if __name__ == "__main__":
    unittest.main()
