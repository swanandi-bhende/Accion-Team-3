"""Test doubles for the Decision Engine's chat model.

These are labelled test doubles: they never call a real LLM provider and must
not be used outside unit tests. A passing test with a fake model proves the
engine's logic, not a real provider connection (that needs the smoke test).
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.runnables import RunnableLambda


class FakeStructuredChatModel:
    """Mimics ``BaseChatModel.with_structured_output`` for unit tests.

    ``respond`` receives the rendered prompt (a ChatPromptValue) and returns
    whatever the structured model would return, or raises to simulate a
    provider failure. ``by_schema`` lets one fake answer differently per
    output schema, e.g. the shared Task Reader (TaskRequirementsBatch) and
    an agent's private judgement (DecisionBatch), as one real shared model
    would. Every rendered prompt is kept so tests can inspect exactly what
    the LLM would have seen.
    """

    def __init__(
        self,
        respond: Callable[[Any], Any] | None = None,
        *,
        by_schema: dict[type, Callable[[Any], Any]] | None = None,
    ):
        self._respond = respond
        self._by_schema = dict(by_schema or {})
        self.calls: list[tuple[type, Any]] = []
        self.schemas: list[type] = []

    def with_structured_output(self, schema: type, **_: Any) -> RunnableLambda:
        self.schemas.append(schema)
        respond = self._by_schema.get(schema, self._respond)

        async def _invoke(prompt_value: Any) -> Any:
            self.calls.append((schema, prompt_value))
            if respond is None:
                raise AssertionError(f"No fake response configured for {schema.__name__}")
            return respond(prompt_value)

        return RunnableLambda(_invoke)

    @property
    def prompts(self) -> list[Any]:
        return [prompt for _, prompt in self.calls]

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, schema: type) -> int:
        return sum(1 for called, _ in self.calls if called is schema)

    def last_prompt_text(self, schema: type | None = None) -> str:
        prompts = [p for s, p in self.calls if schema is None or s is schema]
        return "\n".join(m.content for m in prompts[-1].to_messages())
