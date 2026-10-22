"""Typed errors raised by the Decision Engine.

The runtime can catch these separately (manual item 56). Every error has a
``retryable`` flag:

- retryable=True  -> a temporary problem (rate limit, timeout, network,
  provider server error). The runtime skips the cycle, waits, and tries
  again instead of stopping the agent.
- retryable=False -> retrying will not help (bad key, unknown model, broken
  configuration, model output that does not fit the schema). The agent
  stops in ERROR as before.

Messages never include API keys or full task inputs.
"""


class DecisionEngineError(RuntimeError):
    """Base class for Decision Engine failures."""

    retryable: bool = False

    def __init__(self, message: str = "", *, retryable: bool | None = None):
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class DecisionEngineModelNotConfigured(DecisionEngineError):
    """No chat model was configured, so the engine cannot make decisions."""


class DecisionModelInvocationError(DecisionEngineError):
    """The model provider call failed (network, auth, server, ...).

    ``retryable`` is set per failure by llm_call.invoke_structured.
    """


class DecisionModelRateLimitError(DecisionModelInvocationError):
    """The provider said "too many requests" (HTTP 429). Free tiers hit this."""

    retryable = True


class DecisionModelTimeoutError(DecisionModelInvocationError):
    """The model call did not finish within the engine's time budget."""

    retryable = True


class DecisionModelOutputError(DecisionEngineError):
    """The model answered, but not with a valid structured result."""
