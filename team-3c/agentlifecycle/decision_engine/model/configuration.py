"""Configuration for the Decision Engine's LangChain model provider.

The existing DecisionModelConfiguration API is retained. Environment-backed
configuration is available through ``from_env`` so credentials are not stored
in source code or committed to the repository.

Supported providers (choose with DECISION_LLM_PROVIDER):

- ``openai``  - key in OPENAI_API_KEY. Paid API.
- ``gemini``  - key in GOOGLE_API_KEY (GEMINI_API_KEY also accepted).
  Google lists a free tier for its Flash models, but free-tier prompts may be
  used by Google to improve its products, so send simulated/demo data only.
  Check https://ai.google.dev/gemini-api/docs/pricing for the current list.
"""

from dataclasses import dataclass, field
import os


class ProviderConfigurationError(ValueError):
    """Raised when provider settings are absent or invalid."""


@dataclass(frozen=True)
class ProviderSpec:
    """Provider-specific defaults. Values are documented in the module docstring."""

    key_env_vars: tuple[str, ...]
    default_model: str
    # None = do not send a temperature; the provider's own default applies.
    default_temperature: float | None


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        key_env_vars=("OPENAI_API_KEY",),
        default_model="gpt-4o-mini",
        default_temperature=0.0,
    ),
    # Google recommends keeping Gemini 3 models at their default temperature
    # (1.0); lower values "may lead to unexpected behavior, such as looping or
    # degraded performance" (ai.google.dev, Gemini 3 guide). Determinism of
    # final decisions comes from the code policy, not from temperature.
    "gemini": ProviderSpec(
        key_env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        default_model="gemini-3.8-flash",
        default_temperature=None,
    ),
}


def supported_providers() -> str:
    return ", ".join(sorted(PROVIDERS))


@dataclass
class DecisionModelConfiguration:
    """Settings for a Decision Engine model.

    The first four fields preserve the constructor/API already used by the
    project. ``api_key`` is runtime-only and must never be logged or committed,
    so it is excluded from ``repr()`` (and therefore from printed/logged config).
    ``temperature=None`` means "use the provider's default".
    """

    provider: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> "DecisionModelConfiguration":
        """Build a configuration from environment variables.

        The provider is checked first, so an unsupported provider is reported
        as such rather than as a missing key. The provider's API key is then
        required. This method does not make a network call.
        """
        provider = os.getenv("DECISION_LLM_PROVIDER", "openai").strip().lower()
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise ProviderConfigurationError(
                f"Unsupported DECISION_LLM_PROVIDER '{provider}'. "
                f"Supported providers: {supported_providers()}."
            )

        api_key = ""
        for env_var in spec.key_env_vars:
            api_key = os.getenv(env_var, "").strip()
            if api_key:
                break
        if not api_key:
            names = " or ".join(spec.key_env_vars)
            raise ProviderConfigurationError(
                f"{names} is not set for provider '{provider}'. Set it in your "
                "environment before initializing the Decision Engine; do not "
                "commit API keys."
            )

        # Unset or empty -> the provider's default model.
        model_name = os.getenv("DECISION_LLM_MODEL", "").strip() or spec.default_model

        raw_temperature = os.getenv("DECISION_LLM_TEMPERATURE", "").strip()
        if raw_temperature:
            try:
                temperature = float(raw_temperature)
            except ValueError as exc:
                raise ProviderConfigurationError(
                    "DECISION_LLM_TEMPERATURE must be a number between 0 and 2."
                ) from exc
            if not 0 <= temperature <= 2:
                raise ProviderConfigurationError(
                    "DECISION_LLM_TEMPERATURE must be between 0 and 2."
                )
        else:
            temperature = spec.default_temperature

        try:
            timeout_seconds = float(os.getenv("DECISION_LLM_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ProviderConfigurationError(
                "DECISION_LLM_TIMEOUT_SECONDS must be greater than zero."
            ) from exc
        if timeout_seconds <= 0:
            raise ProviderConfigurationError(
                "DECISION_LLM_TIMEOUT_SECONDS must be greater than zero."
            )

        try:
            max_retries = int(os.getenv("DECISION_LLM_MAX_RETRIES", "1"))
        except ValueError as exc:
            raise ProviderConfigurationError(
                "DECISION_LLM_MAX_RETRIES must be a non-negative integer."
            ) from exc
        if max_retries < 0:
            raise ProviderConfigurationError(
                "DECISION_LLM_MAX_RETRIES must be a non-negative integer."
            )

        return cls(
            provider=provider,
            model_name=model_name,
            temperature=temperature,
            enabled=True,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
