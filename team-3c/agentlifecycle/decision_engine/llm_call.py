"""One place that runs a structured LLM call safely.

Used by both the shared Task Reader (Step 1) and each agent's private
Decision Engine (Step 2), so every LLM call gets the same time budget, the
same typed errors and the same retryable/not-retryable classification.
Error messages never include keys or task text.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelError,
    ModelRateLimitError,
    ModelTimeoutError,
    OutputParserException,
)
from pydantic import ValidationError

from .errors import (
    DecisionModelInvocationError,
    DecisionModelOutputError,
    DecisionModelRateLimitError,
    DecisionModelTimeoutError,
)

# Provider-neutral LangChain error types (used by both langchain-google-genai
# and langchain-openai). Temporary problems are worth retrying; everything
# else (authentication, permission, unknown model, invalid request) is not.
_RETRYABLE_MODEL_ERRORS = (ModelAPIError, ModelConnectionError, ModelTimeoutError)


def _is_temporary_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # httpx/httpcore transport errors, without importing those packages.
    name = type(exc).__name__
    return name.endswith(("ConnectError", "ReadError", "WriteError", "RemoteProtocolError"))


async def invoke_structured(
    chain: Any,
    payload: dict[str, Any],
    expected_type: type,
    *,
    timeout_seconds: float | None,
    label: str,
) -> Any:
    """Run ``chain.ainvoke(payload)`` and return an ``expected_type`` instance.

    ``label`` identifies the caller in error messages (e.g. an agent ID).
    """
    try:
        call = chain.ainvoke(payload)
        if timeout_seconds is not None:
            response = await asyncio.wait_for(call, timeout_seconds)
        else:
            response = await call
    except asyncio.TimeoutError as exc:
        raise DecisionModelTimeoutError(
            f"Model call exceeded {timeout_seconds}s ({label})"
        ) from exc
    except (OutputParserException, ValidationError) as exc:
        raise DecisionModelOutputError(
            f"Model output failed schema validation ({label}): {type(exc).__name__}"
        ) from exc
    except ModelRateLimitError as exc:
        raise DecisionModelRateLimitError(
            f"Provider rate limit reached ({label}): {type(exc).__name__}"
        ) from exc
    except ModelError as exc:
        raise DecisionModelInvocationError(
            f"Model call failed ({label}): {type(exc).__name__}",
            retryable=isinstance(exc, _RETRYABLE_MODEL_ERRORS),
        ) from exc
    except Exception as exc:
        raise DecisionModelInvocationError(
            f"Model call failed ({label}): {type(exc).__name__}",
            retryable=_is_temporary_transport_error(exc),
        ) from exc

    if not isinstance(response, expected_type):
        raise DecisionModelOutputError(
            f"Model returned {type(response).__name__}, expected "
            f"{expected_type.__name__} ({label})"
        )
    return response
