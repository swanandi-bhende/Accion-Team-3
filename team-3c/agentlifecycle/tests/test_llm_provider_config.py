"""Unit tests for Decision Engine provider configuration and construction."""

import os
import unittest
from unittest.mock import patch

from decision_engine.model.configuration import (
    DecisionModelConfiguration,
    ProviderConfigurationError,
)
from decision_engine.model.provider import create_chat_model, create_provider


class DecisionModelConfigurationTests(unittest.TestCase):
    def test_missing_api_key_fails_clearly(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "OPENAI_API_KEY"):
                DecisionModelConfiguration.from_env()

    def test_environment_settings_are_loaded(self):
        values = {
            "OPENAI_API_KEY": "test-key-not-a-real-secret",
            "DECISION_LLM_PROVIDER": "openai",
            "DECISION_LLM_MODEL": "test-model",
            "DECISION_LLM_TEMPERATURE": "0",
            "DECISION_LLM_TIMEOUT_SECONDS": "12",
            "DECISION_LLM_MAX_RETRIES": "2",
        }
        with patch.dict(os.environ, values, clear=True):
            config = DecisionModelConfiguration.from_env()
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model_name, "test-model")
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.timeout_seconds, 12.0)
        self.assertEqual(config.max_retries, 2)
        self.assertTrue(config.enabled)

    def test_invalid_timeout_is_rejected(self):
        values = {"OPENAI_API_KEY": "test-key", "DECISION_LLM_TIMEOUT_SECONDS": "0"}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "DECISION_LLM_TIMEOUT_SECONDS"):
                DecisionModelConfiguration.from_env()

    def test_invalid_retry_count_is_rejected(self):
        values = {"OPENAI_API_KEY": "test-key", "DECISION_LLM_MAX_RETRIES": "-1"}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "non-negative"):
                DecisionModelConfiguration.from_env()

    def test_unsupported_provider_is_rejected(self):
        values = {"OPENAI_API_KEY": "test-key", "DECISION_LLM_PROVIDER": "unknown"}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "Unsupported"):
                DecisionModelConfiguration.from_env()

    def test_unsupported_provider_is_reported_before_missing_key(self):
        values = {"DECISION_LLM_PROVIDER": "unknown"}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "Unsupported"):
                DecisionModelConfiguration.from_env()

    def test_api_key_is_not_exposed_in_repr(self):
        secret = "test-secret-value-should-not-appear"
        config = DecisionModelConfiguration(
            provider="openai", model_name="test-model", api_key=secret, enabled=True
        )
        self.assertNotIn(secret, repr(config))
        self.assertNotIn(secret, str(config))
        self.assertEqual(config.api_key, secret)

    def test_disabled_configuration_is_rejected(self):
        config = DecisionModelConfiguration(
            provider="openai", model_name="test-model", api_key="test-key", enabled=False
        )
        with self.assertRaisesRegex(ProviderConfigurationError, "disabled"):
            create_chat_model(config)

    def test_provider_wrapper_uses_created_model(self):
        model = object()
        with patch("decision_engine.model.provider.create_chat_model", return_value=model):
            provider = create_provider(DecisionModelConfiguration(enabled=True))
        self.assertIs(provider.get_model(), model)

    def test_factory_passes_settings_to_chatopenai(self):
        from unittest.mock import MagicMock

        fake_module = MagicMock()
        fake_module.ChatOpenAI.return_value = object()
        config = DecisionModelConfiguration(
            provider="openai", model_name="test-model", temperature=0.0,
            enabled=True, api_key="test-key", timeout_seconds=9, max_retries=3,
        )
        with patch.dict("sys.modules", {"langchain_openai": fake_module}):
            create_chat_model(config)
        fake_module.ChatOpenAI.assert_called_once_with(
            model="test-model", api_key="test-key", temperature=0.0,
            timeout=9, max_retries=3,
        )


class GeminiProviderTests(unittest.TestCase):
    def test_gemini_settings_load_from_google_api_key(self):
        values = {"DECISION_LLM_PROVIDER": "gemini", "GOOGLE_API_KEY": "g-key"}
        with patch.dict(os.environ, values, clear=True):
            config = DecisionModelConfiguration.from_env()
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.api_key, "g-key")
        self.assertEqual(config.model_name, "gemini-3.8-flash")
        # Google recommends the default temperature for Gemini 3 models.
        self.assertIsNone(config.temperature)

    def test_gemini_api_key_is_accepted_as_fallback(self):
        values = {"DECISION_LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "g2-key"}
        with patch.dict(os.environ, values, clear=True):
            config = DecisionModelConfiguration.from_env()
        self.assertEqual(config.api_key, "g2-key")

    def test_missing_gemini_key_names_the_right_variable(self):
        values = {"DECISION_LLM_PROVIDER": "gemini", "OPENAI_API_KEY": "wrong-provider"}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "GOOGLE_API_KEY"):
                DecisionModelConfiguration.from_env()

    def test_empty_optional_values_fall_back_to_provider_defaults(self):
        values = {
            "DECISION_LLM_PROVIDER": "gemini",
            "GOOGLE_API_KEY": "g-key",
            "DECISION_LLM_MODEL": "",
            "DECISION_LLM_TEMPERATURE": "",
        }
        with patch.dict(os.environ, values, clear=True):
            config = DecisionModelConfiguration.from_env()
        self.assertEqual(config.model_name, "gemini-3.8-flash")
        self.assertIsNone(config.temperature)

    def test_factory_builds_gemini_model_without_forcing_temperature(self):
        from unittest.mock import MagicMock

        fake_module = MagicMock()
        fake_module.ChatGoogleGenerativeAI.return_value = object()
        config = DecisionModelConfiguration(
            provider="gemini", model_name="gemini-test", enabled=True,
            api_key="g-key", timeout_seconds=12, max_retries=2,
        )
        with patch.dict("sys.modules", {"langchain_google_genai": fake_module}):
            create_chat_model(config)
        fake_module.ChatGoogleGenerativeAI.assert_called_once_with(
            model="gemini-test", api_key="g-key", timeout=12, max_retries=2,
        )

    def test_real_gemini_client_constructs_and_hides_key(self):
        # Builds the real LangChain client object; makes no network call.
        config = DecisionModelConfiguration(
            provider="gemini", model_name="gemini-3.8-flash", enabled=True,
            api_key="g-secret-should-not-appear", timeout_seconds=30, max_retries=1,
        )
        model = create_chat_model(config)
        self.assertEqual(type(model).__name__, "ChatGoogleGenerativeAI")
        self.assertNotIn("g-secret-should-not-appear", repr(model))


if __name__ == "__main__":
    unittest.main()
