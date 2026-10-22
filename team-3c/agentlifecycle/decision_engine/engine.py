"""Decision Engine: is each available task suitable for this agent?

Flow of ``evaluate_tasks`` (cheapest checks first, LLM calls last):

1. Capacity: if the agent has no free task slots, reject everything
   (capacity_full). No LLM call.
2. Basic checks in code: task open, deadline not passed. No LLM call.
3. Step 1 - shared Task Reader: tasks that did not declare their required
   capabilities are read ONCE (shared by all agents, cached) and turned
   into catalogue capability IDs. Skipped entirely if this agent has no
   verified capabilities.
4. Capability check in code: every required capability must be in this
   agent's verified manifest; unsupported needs can never be met.
5. Step 2 - this agent's private LLM call, only for tasks that passed.
6. Validate the answer: unknown task IDs are logged and dropped, duplicate
   or missing decisions reject that task, and the model must say suitable
   AND reach the score threshold.
7. Apply the free-slot limit, best scores first.
8. Report and record only NEW or CHANGED decisions (see "memory of
   decisions" below), in this agent's private history.

Memory of decisions (so a live loop does not re-ask the LLM every cycle or
send the same task to bidding twice):
- FINAL outcomes are never re-judged while the task's description and
  declared requirements stay the same: suitable, capability_missing,
  model_not_suitable, score_below_threshold, deadline_passed,
  requirements_unspecified.
- TEMPORARY outcomes are checked again next cycle (capacity_full,
  requirements_unreadable, missing/duplicate model decision, task_not_open);
  an unchanged repeat is not reported or recorded again.
- A changed description or declared requirements = a new task: judged again.
- The memory is per agent and in-memory (lost on restart; persistent
  history is WP4's event store).

The engine never calculates a price, submits a bid or runs a workload.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from .context.agent_context import AgentDecisionContext
from .errors import DecisionEngineModelNotConfigured
from .llm_call import invoke_structured
from .logging.decision_logger import log_decision, log_decision_warning
from .memory.history import DecisionHistory
from .models.decision import DecisionBatch, DecisionRecord, DecisionResult
from .models.task import MarketplaceTask
from .policy.decision_policy import DecisionPolicy, RejectionReason
from .reasoning.prompts import (
    DECISION_PROMPT,
    DECISION_PROMPT_VERSION,
    NO_HISTORY_MARKER,
)
from .task_reading.models import TaskRequirements
from .task_reading.reader import requirements_without_reading

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from .task_reading.reader import TaskReader

# Only these record fields are shown to the model; metadata stays internal.
_HISTORY_PROMPT_FIELDS = {
    "task_id", "suitable", "match_score", "rejection_reasons", "outcome",
}

# Kept importable from here for existing callers.
__all__ = ["DecisionEngine", "DecisionEngineModelNotConfigured"]

ActiveTaskCounter = Callable[[str], Awaitable[int]]

# Outcomes that cannot change while the task itself stays the same.
FINAL_REJECTIONS = frozenset({
    RejectionReason.CAPABILITY_MISSING.value,
    RejectionReason.MODEL_NOT_SUITABLE.value,
    RejectionReason.SCORE_BELOW_THRESHOLD.value,
    RejectionReason.DEADLINE_PASSED.value,
    RejectionReason.REQUIREMENTS_UNSPECIFIED.value,
})


def task_fingerprint(task: MarketplaceTask) -> str:
    """Identity of a task's content: description + declared requirements."""
    raw = json.dumps(
        [task.description or "", sorted(task.required_capabilities)],
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class _Remembered:
    fingerprint: str
    outcome: tuple
    final: bool


def _outcome_of(result: DecisionResult) -> tuple:
    return (result.suitable, tuple(result.rejection_reasons))


def _is_final(result: DecisionResult) -> bool:
    if result.suitable:
        return True
    return bool(result.rejection_reasons) and set(result.rejection_reasons) <= FINAL_REJECTIONS


class DecisionEngine:
    CONTEXT_WINDOW_SIZE = 20
    DEFAULT_INVOCATION_TIMEOUT_SECONDS = 60.0
    MAX_REMEMBERED_TASKS = 5000

    def __init__(
        self,
        agent_context: AgentDecisionContext,
        model: BaseChatModel | None = None,
        history: DecisionHistory | None = None,
        policy: DecisionPolicy | None = None,
        *,
        task_reader: TaskReader | None = None,
        active_task_counter: ActiveTaskCounter | None = None,
        invocation_timeout_seconds: float | None = DEFAULT_INVOCATION_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self.agent_context = agent_context
        self.model = model
        self.history = history if history is not None else DecisionHistory()
        self.policy = policy or DecisionPolicy()
        # Shared Step 1 reader (one instance for all agents, from the
        # factory). Without it, only tasks that declare their requirements
        # can be evaluated; others get requirements_unspecified.
        self.task_reader = task_reader
        # Source of the agent's real number of active (awarded) tasks. Until
        # the award/event path exists (WP3/WP4), the count comes from the
        # agent context; no awarded tasks exist yet, so 0 is currently true.
        self._active_task_counter = active_task_counter
        # Overall budget for one model call, including provider retries, so a
        # hung call cannot block pause()/shutdown() forever.
        self._invocation_timeout = invocation_timeout_seconds
        self._clock = clock
        # Per-agent memory of decisions already made (see module docstring).
        self._remembered: OrderedDict[str, _Remembered] = OrderedDict()
        # Number of private (Step 2) LLM calls this engine has made.
        self.model_calls = 0
        self.chain = None
        if model is not None:
            self._build_chain(model)

    @property
    def ready(self) -> bool:
        if self.chain is None:
            return False
        return self.task_reader is None or self.task_reader.ready

    def _build_chain(self, model: BaseChatModel):
        self.chain = DECISION_PROMPT | model.with_structured_output(DecisionBatch)

    def attach_model(self, model: BaseChatModel):
        self.model = model
        self._build_chain(model)

    async def _active_task_count(self) -> int:
        if self._active_task_counter is None:
            return self.agent_context.active_task_count
        return await self._active_task_counter(self.agent_context.agent_id)

    # ------------------------------------------------------------------
    # Main entry point used by the runtime's decision cycle
    # ------------------------------------------------------------------

    async def evaluate_tasks(self, tasks: list[MarketplaceTask]) -> list[DecisionResult]:
        if not self.ready:
            raise DecisionEngineModelNotConfigured(
                "No LangChain chat model is configured."
            )

        agent_id = self.agent_context.agent_id
        tasks = self._dedupe_input_tasks(tasks)
        fingerprints = {task.task_id: task_fingerprint(task) for task in tasks}

        # Final decisions on unchanged tasks are not made again.
        already_decided = {
            task.task_id for task in tasks
            if self._is_settled(task.task_id, fingerprints[task.task_id])
        }
        if already_decided:
            tasks = [t for t in tasks if t.task_id not in already_decided]
            log_decision(
                "skipped tasks already decided",
                agent_id=agent_id,
                count=len(already_decided),
            )
        if not tasks:
            return []

        results: dict[str, DecisionResult] = {}

        # 1. No free slots: nothing can be pursued, so no LLM calls at all.
        free_slots = self.policy.free_slots(
            await self._active_task_count(),
            self.agent_context.max_concurrent_tasks,
        )
        if free_slots == 0:
            for task in tasks:
                results[task.task_id] = self._rejected(
                    task.task_id, [RejectionReason.CAPACITY_FULL]
                )
            return self._finish(results, fingerprints)

        # 2. Exact on-chain facts.
        now = self._clock()
        open_tasks: list[MarketplaceTask] = []
        for task in tasks:
            check = self.policy.basic_precheck(task, now)
            if check.passed:
                open_tasks.append(task)
            else:
                results[task.task_id] = self._rejected(task.task_id, check.reasons)

        if not open_tasks:
            return self._finish(results, fingerprints)

        # An agent with no verified capabilities can never meet any need,
        # so do not spend a shared reading on its behalf.
        if not self.agent_context.capabilities:
            for task in open_tasks:
                results[task.task_id] = self._rejected(
                    task.task_id,
                    [RejectionReason.CAPABILITY_MISSING],
                    capability_gap=list(task.required_capabilities),
                )
            return self._finish(results, fingerprints)

        # 3. Step 1: what does each task need? (shared, cached reading)
        requirements = await self._requirements_for(open_tasks)

        # 4. Code: does THIS agent have all of it?
        candidates: list[MarketplaceTask] = []
        for task in open_tasks:
            reading = requirements[task.task_id]
            check = self.policy.capability_check(reading, self.agent_context.capabilities)
            if check.passed:
                # The private call sees the requirements as read in Step 1.
                candidates.append(
                    task.model_copy(
                        update={"required_capabilities": reading.required_capabilities}
                    )
                )
                continue
            results[task.task_id] = self._rejected(
                task.task_id,
                check.reasons,
                capability_gap=check.capability_gap,
                requirements=reading,
            )
            if check.capability_gap:
                # Requirement 2.4: log the capability gap.
                log_decision(
                    "capability gap",
                    agent_id=agent_id,
                    task_id=task.task_id,
                    missing=check.capability_gap,
                    requirements_source=reading.source.value,
                )

        if not candidates:
            return self._finish(results, fingerprints)

        # 5. Step 2: this agent's private judgement, candidates only.
        batch = await self._invoke_model(candidates)

        # 6. Validate the answer against the tasks actually sent.
        sent_ids = {task.task_id for task in candidates}
        counts = Counter(decision.task_id for decision in batch.decisions)
        unknown = sorted(task_id for task_id in counts if task_id not in sent_ids)
        if unknown:
            log_decision_warning(
                "model returned unknown task ids; ignored",
                agent_id=agent_id,
                unknown_task_ids=unknown,
            )

        by_id = {d.task_id: d for d in batch.decisions if d.task_id in sent_ids}
        eligible = []
        for task in candidates:
            task_id = task.task_id
            reading = requirements[task_id]
            if counts[task_id] == 0:
                results[task_id] = self._rejected(
                    task_id, [RejectionReason.MISSING_MODEL_DECISION], requirements=reading
                )
                continue
            if counts[task_id] > 1:
                results[task_id] = self._rejected(
                    task_id, [RejectionReason.DUPLICATE_TASK_DECISION], requirements=reading
                )
                continue

            decision = by_id[task_id]
            reasons = self.policy.review_model_decision(decision)
            results[task_id] = DecisionResult(
                task_id=task_id,
                suitable=not reasons,
                match_score=decision.match_score,
                model_suitable=decision.suitable,
                rejection_reasons=[r.value for r in reasons],
                required_capabilities=list(reading.required_capabilities),
                requirements_source=reading.source.value,
                estimated_completion_seconds=decision.estimated_completion_seconds,
                reasoning=decision.reasoning,
            )
            if not reasons:
                eligible.append(results[task_id])

        # 7. Respect the free-slot limit: best scores first, stable by ID.
        eligible.sort(key=lambda r: (-r.match_score, r.task_id))
        for result in eligible[free_slots:]:
            result.suitable = False
            result.rejection_reasons = [RejectionReason.CAPACITY_FULL.value]

        return self._finish(results, fingerprints)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _requirements_for(
        self, tasks: list[MarketplaceTask]
    ) -> dict[str, TaskRequirements]:
        if self.task_reader is not None:
            return await self.task_reader.read(tasks)
        # No reader configured: declared requirements only.
        readings: dict[str, TaskRequirements] = {}
        for task in tasks:
            known = requirements_without_reading(task)
            readings[task.task_id] = known or TaskRequirements(
                task_id=task.task_id,
                source="unspecified",
                reasoning="No task reader configured to read the description.",
            )
        return readings

    def _dedupe_input_tasks(self, tasks: list[MarketplaceTask]) -> list[MarketplaceTask]:
        seen: set[str] = set()
        unique: list[MarketplaceTask] = []
        for task in tasks:
            if task.task_id in seen:
                log_decision_warning(
                    "duplicate task id from marketplace; keeping first",
                    agent_id=self.agent_context.agent_id,
                    task_id=task.task_id,
                )
                continue
            seen.add(task.task_id)
            unique.append(task)
        return unique

    @staticmethod
    def _rejected(task_id, reasons, capability_gap=None, requirements=None) -> DecisionResult:
        return DecisionResult(
            task_id=task_id,
            suitable=False,
            rejection_reasons=[r.value for r in reasons],
            capability_gap=list(capability_gap or []),
            required_capabilities=(
                list(requirements.required_capabilities) if requirements else []
            ),
            requirements_source=requirements.source.value if requirements else None,
        )

    def _history_for_prompt(self) -> str:
        """Real recorded decisions only; an explicit marker when there are none."""
        records = self.history.recent()
        if not records:
            return NO_HISTORY_MARKER
        return json.dumps(
            [r.model_dump(include=_HISTORY_PROMPT_FIELDS) for r in records]
        )

    def _model_label(self) -> str:
        return (
            getattr(self.model, "model_name", None)
            or getattr(self.model, "model", None)
            or type(self.model).__name__
        )

    async def _invoke_model(self, candidates: list[MarketplaceTask]) -> DecisionBatch:
        payload = {
            "agent_context": self.agent_context.model_dump_json(),
            "history": self._history_for_prompt(),
            "tasks": json.dumps([t.model_dump() for t in candidates]),
            "market_context": json.dumps(
                {t.task_id: t.market_context for t in candidates}
            ),
        }
        self.model_calls += 1
        return await invoke_structured(
            self.chain,
            payload,
            DecisionBatch,
            timeout_seconds=self._invocation_timeout,
            label=f"agent {self.agent_context.agent_id}",
        )

    def _is_settled(self, task_id: str, fingerprint: str) -> bool:
        remembered = self._remembered.get(task_id)
        return (
            remembered is not None
            and remembered.final
            and remembered.fingerprint == fingerprint
        )

    def _remember(self, result: DecisionResult, fingerprint: str) -> bool:
        """Store the outcome; return False if it is an unchanged repeat."""
        task_id = result.task_id
        previous = self._remembered.get(task_id)
        outcome = _outcome_of(result)
        if (
            previous is not None
            and previous.fingerprint == fingerprint
            and previous.outcome == outcome
        ):
            self._remembered.move_to_end(task_id)
            return False
        self._remembered[task_id] = _Remembered(fingerprint, outcome, _is_final(result))
        self._remembered.move_to_end(task_id)
        while len(self._remembered) > self.MAX_REMEMBERED_TASKS:
            self._remembered.popitem(last=False)
        return True

    def _finish(
        self,
        results: dict[str, DecisionResult],
        fingerprints: dict[str, str],
    ) -> list[DecisionResult]:
        # Report and record only new or changed decisions.
        results = {
            task_id: result
            for task_id, result in results.items()
            if self._remember(result, fingerprints[task_id])
        }
        ordered = sorted(
            results.values(),
            key=lambda r: (
                r.match_score is None,
                -(r.match_score or 0.0),
                r.task_id,
            ),
        )
        metadata = {
            "prompt_version": DECISION_PROMPT_VERSION,
            "model": str(self._model_label()),
            "decided_at": self._clock(),
        }
        for result in ordered:
            self.history.add(
                DecisionRecord(
                    task_id=result.task_id,
                    suitable=result.suitable,
                    match_score=result.match_score,
                    rejection_reasons=list(result.rejection_reasons),
                    metadata={
                        **metadata,
                        "evaluated_by_model": result.model_suitable is not None,
                        "requirements_source": result.requirements_source,
                    },
                )
            )
        log_decision(
            "tasks evaluated",
            agent_id=self.agent_context.agent_id,
            evaluated=len(ordered),
            suitable=sum(r.suitable for r in ordered),
        )
        return ordered
