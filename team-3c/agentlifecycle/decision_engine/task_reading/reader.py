"""Step 1: the shared Task Reader.

Team 3A's on-chain task has no capability field - only a description hash -
so someone must read each task's description and turn it into capability
IDs. This reader does that ONCE per task and caches the result, and every
agent's Decision Engine reuses it. Benefits: one consistent reading per
task, and far fewer LLM calls than reading per agent.

The reader never judges any agent. Its answer is still a proposal: code
keeps only catalogue IDs, and rejects missing or duplicated answers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter, OrderedDict
from typing import TYPE_CHECKING, Any

from ..llm_call import invoke_structured
from ..logging.decision_logger import log_decision, log_decision_warning
from ..models.task import MarketplaceTask
from .catalogue import CapabilityCatalogue
from .models import (
    RequirementsSource,
    TaskRequirements,
    TaskRequirementsBatch,
)
from .prompts import READER_PROMPT, READER_PROMPT_VERSION

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


def requirements_without_reading(task: MarketplaceTask) -> TaskRequirements | None:
    """Requirements that need no LLM, or None when the task must be read.

    - Declared requirements (e.g. simulator listings) are used as given.
    - A task with neither declared requirements nor a description cannot
      be read at all.
    """
    declared = list(dict.fromkeys(c for c in task.required_capabilities if c))
    if declared:
        return TaskRequirements(
            task_id=task.task_id,
            source=RequirementsSource.DECLARED,
            required_capabilities=declared,
        )
    if not (task.description or "").strip():
        return TaskRequirements(
            task_id=task.task_id,
            source=RequirementsSource.UNSPECIFIED,
            reasoning="No declared requirements and no description to read.",
        )
    return None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


class TaskReader:
    DEFAULT_MAX_CACHE_ENTRIES = 1000

    def __init__(
        self,
        model: BaseChatModel,
        catalogue: CapabilityCatalogue,
        *,
        invocation_timeout_seconds: float | None = 60.0,
        max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        self.catalogue = catalogue
        self._chain = READER_PROMPT | model.with_structured_output(TaskRequirementsBatch)
        self._timeout = invocation_timeout_seconds
        self._max_cache_entries = max_cache_entries
        self._cache: OrderedDict[str, TaskRequirements] = OrderedDict()
        # Several agents may poll at the same moment; the lock makes sure an
        # uncached task is still read only once.
        self._lock = asyncio.Lock()
        self.model_calls = 0

    @property
    def ready(self) -> bool:
        return self._chain is not None

    def _cache_key(self, task: MarketplaceTask) -> str:
        # Key on the exact text the model reads, the catalogue and the
        # prompt version: if any of them changes, the task is read again.
        text_hash = hashlib.sha256((task.description or "").encode("utf-8")).hexdigest()
        return "|".join(
            [task.task_id, text_hash, self.catalogue.fingerprint, READER_PROMPT_VERSION]
        )

    def peek(self, task: MarketplaceTask) -> TaskRequirements | None:
        """The reading already known for a task, without any LLM call."""
        known = requirements_without_reading(task)
        if known is not None:
            return known
        return self._cache.get(self._cache_key(task))

    async def read(self, tasks: list[MarketplaceTask]) -> dict[str, TaskRequirements]:
        results: dict[str, TaskRequirements] = {}
        to_read: list[MarketplaceTask] = []

        for task in tasks:
            known = requirements_without_reading(task)
            if known is not None:
                results[task.task_id] = known
                continue
            cached = self._cache.get(self._cache_key(task))
            if cached is not None:
                results[task.task_id] = cached
            else:
                to_read.append(task)

        if not to_read:
            return results

        async with self._lock:
            # Another agent may have read these while we waited.
            still_unread = [t for t in to_read if self._cache_key(t) not in self._cache]
            if still_unread:
                readings = await self._read_with_model(still_unread)
                for task in still_unread:
                    reading = readings[task.task_id]
                    # Unreadable answers are not cached, so the next cycle
                    # gets a fresh attempt.
                    if reading.source is not RequirementsSource.UNREADABLE:
                        self._store(self._cache_key(task), reading)
                    results[task.task_id] = reading
            for task in to_read:
                if task.task_id not in results:
                    results[task.task_id] = self._cache[self._cache_key(task)]

        return results

    def _store(self, key: str, reading: TaskRequirements) -> None:
        self._cache[key] = reading
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_cache_entries:
            self._cache.popitem(last=False)

    async def _read_with_model(
        self, tasks: list[MarketplaceTask]
    ) -> dict[str, TaskRequirements]:
        # Only the ID and description are sent: the reader needs nothing
        # else (no bounty, creator or market data).
        payload: dict[str, Any] = {
            "catalogue": self.catalogue.for_prompt(),
            "tasks": json.dumps(
                [{"task_id": t.task_id, "description": t.description} for t in tasks]
            ),
        }
        self.model_calls += 1
        batch = await invoke_structured(
            self._chain,
            payload,
            TaskRequirementsBatch,
            timeout_seconds=self._timeout,
            label="task reader",
        )

        sent_ids = {t.task_id for t in tasks}
        counts = Counter(answer.task_id for answer in batch.tasks)
        unknown = sorted(tid for tid in counts if tid not in sent_ids)
        if unknown:
            log_decision_warning(
                "task reader returned unknown task ids; ignored",
                unknown_task_ids=unknown,
            )

        answers = {a.task_id: a for a in batch.tasks if a.task_id in sent_ids}
        readings: dict[str, TaskRequirements] = {}
        for task in tasks:
            task_id = task.task_id
            if counts[task_id] != 1:
                readings[task_id] = TaskRequirements(
                    task_id=task_id,
                    source=RequirementsSource.UNREADABLE,
                    reasoning=(
                        "Reader gave no answer for this task."
                        if counts[task_id] == 0
                        else "Reader answered this task more than once."
                    ),
                )
                continue

            answer = answers[task_id]
            proposed = _unique(answer.required_capabilities)
            in_catalogue = [c for c in proposed if c in self.catalogue]
            # IDs outside the catalogue are not trusted as IDs; they are
            # kept as unsupported needs so no agent can match them.
            outside = [c for c in proposed if c not in self.catalogue]
            unsupported = _unique(answer.unsupported_requirements + outside)

            if not in_catalogue and not unsupported:
                source = RequirementsSource.UNSPECIFIED
            else:
                source = RequirementsSource.EXTRACTED
            readings[task_id] = TaskRequirements(
                task_id=task_id,
                source=source,
                required_capabilities=in_catalogue,
                unsupported_requirements=unsupported,
                reasoning=answer.reasoning,
            )

        log_decision(
            "tasks read",
            read=len(tasks),
            extracted=sum(
                r.source is RequirementsSource.EXTRACTED for r in readings.values()
            ),
        )
        return readings
