"""Build one private Decision Engine per agent.

Shared across all agents (created once, held by the factory):
  - the LangChain chat model client (one API key for the whole project)
  - the Task Reader (Step 1): reads each task description once, cached
  - the prompt templates (module-level in reasoning/prompts.py)
  - the default decision policy

Private to each agent (created fresh in ``create``):
  - the agent decision context (identity, verified capabilities, limits)
  - the decision history
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .context.agent_context import AgentDecisionContext
from .engine import DecisionEngine
from .memory.history import DecisionHistory
from .policy.constraints import DEFAULT_MAX_CONCURRENT_TASKS
from .policy.decision_policy import DecisionPolicy
from .task_reading.catalogue import CapabilityCatalogue
from .task_reading.reader import TaskReader

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from .model.configuration import DecisionModelConfiguration


class AgentContextError(ValueError):
    """Raised when an agent definition cannot produce a valid decision context."""


def build_agent_decision_context(definition: Any) -> AgentDecisionContext:
    """Translate a stored AgentDefinition into the Decision Engine's context.

    Only ``capability_manifest`` is used for capabilities: it is the list the
    runtime verifies against registered handlers during initialize(). The
    legacy ``capabilities`` field and the ``performance`` dict are ignored on
    purpose, so unverified skills or unrecorded metrics never reach the LLM.
    """
    capabilities = list(dict.fromkeys(definition.capability_manifest))

    configuration = definition.configuration or {}
    max_tasks = configuration.get(
        "max_concurrent_tasks", DEFAULT_MAX_CONCURRENT_TASKS
    )
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks < 1:
        raise AgentContextError(
            f"Agent {definition.agent_id}: configuration.max_concurrent_tasks "
            f"must be a positive integer, got {max_tasks!r}."
        )

    return AgentDecisionContext(
        agent_id=definition.agent_id,
        capabilities=capabilities,
        max_concurrent_tasks=max_tasks,
    )


class DecisionEngineFactory:
    """Create a separate DecisionEngine for every agent the runtime loads."""

    def __init__(
        self,
        model: BaseChatModel | None,
        *,
        policy: DecisionPolicy | None = None,
        history_size: int = DecisionHistory.CONTEXT_WINDOW_SIZE,
        active_task_counter: Any = None,
        invocation_timeout_seconds: float | None = (
            DecisionEngine.DEFAULT_INVOCATION_TIMEOUT_SECONDS
        ),
        capability_catalogue: CapabilityCatalogue | None = None,
    ) -> None:
        # model=None is allowed so the runtime can still load agents, but
        # every engine will report ready=False and initialize() will fail.
        self._model = model
        self._policy = policy or DecisionPolicy()
        self._history_size = history_size
        # async (agent_id) -> int. To be supplied from real award/event
        # records (WP4). See DecisionEngine for the behaviour when absent.
        self._active_task_counter = active_task_counter
        self._invocation_timeout = invocation_timeout_seconds
        # One reader for ALL agents, so each task is read once and every
        # agent sees the same reading. Without a catalogue there is nothing
        # to map descriptions onto, so only declared requirements are used.
        self.task_reader: TaskReader | None = None
        if model is not None and capability_catalogue is not None:
            self.task_reader = TaskReader(
                model,
                capability_catalogue,
                invocation_timeout_seconds=invocation_timeout_seconds,
            )

    @classmethod
    def from_configuration(
        cls,
        configuration: DecisionModelConfiguration | None = None,
        **kwargs: Any,
    ) -> "DecisionEngineFactory":
        """Build the shared model from settings (environment by default).

        Raises ProviderConfigurationError when credentials/settings are
        missing, so startup fails clearly instead of pretending to be ready.
        """
        from .model.provider import create_chat_model

        return cls(create_chat_model(configuration), **kwargs)

    @property
    def model_configured(self) -> bool:
        return self._model is not None

    def create(self, definition: Any) -> DecisionEngine:
        return DecisionEngine(
            agent_context=build_agent_decision_context(definition),
            model=self._model,
            history=DecisionHistory(max_size=self._history_size),
            policy=self._policy,
            task_reader=self.task_reader,
            active_task_counter=self._active_task_counter,
            invocation_timeout_seconds=self._invocation_timeout,
        )
