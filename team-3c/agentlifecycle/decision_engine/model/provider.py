"""LangChain provider abstraction and concrete provider factory (OpenAI, Gemini).

Everything outside this module only sees a LangChain ``BaseChatModel``, so
switching providers never touches the Decision Engine itself.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .configuration import (
    PROVIDERS,
    DecisionModelConfiguration,
    ProviderConfigurationError,
    supported_providers,
)

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


class ProviderDependencyError(RuntimeError):
    """Raised when the selected provider's integration package is unavailable."""


class DecisionModelProvider(ABC):
    @abstractmethod
    def get_model(self) -> "BaseChatModel":
        raise NotImplementedError


class ConfiguredDecisionModelProvider(DecisionModelProvider):
    """Provider wrapper retained for the existing dependency-injection design."""

    def __init__(self, model: "BaseChatModel"):
        self._model = model

    def get_model(self) -> "BaseChatModel":
        return self._model


def create_chat_model(
    configuration: DecisionModelConfiguration | None = None,
) -> "BaseChatModel":
    """Create the configured LangChain chat model without making an API call.

    When no configuration is passed, settings are loaded from environment
    variables. A successful return means the client was constructed, not that
    credentials/network access have been verified; use a real provider smoke
    test for that verification.
    """
    if configuration is None:
        configuration = DecisionModelConfiguration.from_env()

    if not isinstance(configuration, DecisionModelConfiguration):
        raise ProviderConfigurationError(
            "configuration must be a DecisionModelConfiguration instance."
        )
    if not configuration.enabled:
        raise ProviderConfigurationError(
            "Decision model configuration is disabled. Load valid settings with "
            "DecisionModelConfiguration.from_env() or explicitly enable a configuration."
        )
    provider = (configuration.provider or "openai").lower()
    if provider not in PROVIDERS:
        raise ProviderConfigurationError(
            f"Unsupported model provider: {configuration.provider!r}. "
            f"Supported providers: {supported_providers()}."
        )
    if not configuration.api_key:
        raise ProviderConfigurationError(
            "No API key is configured. Use DecisionModelConfiguration.from_env()."
        )
    if not configuration.model_name:
        raise ProviderConfigurationError("No model name is configured.")
    if configuration.timeout_seconds <= 0:
        raise ProviderConfigurationError("timeout_seconds must be greater than zero.")
    if configuration.max_retries < 0:
        raise ProviderConfigurationError("max_retries must be non-negative.")

    kwargs = {
        "model": configuration.model_name,
        "api_key": configuration.api_key,
        "timeout": configuration.timeout_seconds,
        "max_retries": configuration.max_retries,
    }
    # Only send a temperature when one is configured; otherwise the
    # provider's own (recommended) default applies.
    if configuration.temperature is not None:
        kwargs["temperature"] = configuration.temperature

    if provider == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise ProviderDependencyError(
                "The langchain-google-genai package is not installed. Install "
                "dependencies from agentlifecycle/requirements.txt."
            ) from exc
        return ChatGoogleGenerativeAI(**kwargs)

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ProviderDependencyError(
            "The langchain-openai package is not installed. Install dependencies from "
            "agentlifecycle/requirements.txt."
        ) from exc
    return ChatOpenAI(**kwargs)


def create_provider(
    configuration: DecisionModelConfiguration | None = None,
) -> ConfiguredDecisionModelProvider:
    """Create the project's existing provider wrapper around the real model."""
    return ConfiguredDecisionModelProvider(create_chat_model(configuration))
