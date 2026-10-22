"""Deterministic policy applied around the LLM (manual Task 3.3).

The model's output is a proposal, not authority. Hard rules written in code
can veto a task; the model can never override a hard veto. The policy
decides eligibility only and never calculates bid amounts (that is the
Bidding Engine's job, WP3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .constraints import MIN_MATCH_SCORE


class RejectionReason(str, Enum):
    """Why a task was judged not eligible. Values are stable strings."""

    # Checked in code BEFORE the agent's private model call (the task never
    # reaches that call). Capability checks use the shared Task Reader's
    # result when the task did not declare its requirements.
    CAPABILITY_MISSING = "capability_missing"
    REQUIREMENTS_UNSPECIFIED = "requirements_unspecified"
    REQUIREMENTS_UNREADABLE = "requirements_unreadable"
    TASK_NOT_OPEN = "task_not_open"
    DEADLINE_PASSED = "deadline_passed"
    CAPACITY_FULL = "capacity_full"

    # Checked in code AFTER the model answers.
    MISSING_MODEL_DECISION = "missing_model_decision"
    DUPLICATE_TASK_DECISION = "duplicate_task_decision"
    MODEL_NOT_SUITABLE = "model_not_suitable"
    SCORE_BELOW_THRESHOLD = "score_below_threshold"


@dataclass(frozen=True)
class PrecheckResult:
    reasons: list[RejectionReason]
    capability_gap: list[str]

    @property
    def passed(self) -> bool:
        return not self.reasons


class DecisionPolicy:
    """Hard rules shared by every agent's Decision Engine."""

    def __init__(self, min_match_score: float = MIN_MATCH_SCORE) -> None:
        if not 0.0 <= min_match_score <= 1.0:
            raise ValueError("min_match_score must be between 0.0 and 1.0")
        self.min_match_score = min_match_score

    def basic_precheck(self, task, now: float) -> PrecheckResult:
        """Exact on-chain facts; needs no LLM at all.

        The task must be open and its deadline (Unix seconds, as in Team
        3A's TaskManager) must not have passed.
        """
        reasons: list[RejectionReason] = []
        if (task.status or "").strip().lower() != "open":
            reasons.append(RejectionReason.TASK_NOT_OPEN)
        if task.deadline is not None and task.deadline <= now:
            reasons.append(RejectionReason.DEADLINE_PASSED)
        return PrecheckResult(reasons=reasons, capability_gap=[])

    def capability_check(
        self,
        requirements,
        verified_capabilities: list[str],
    ) -> PrecheckResult:
        """Does this agent have everything the task needs?

        ``requirements`` is a task_reading.TaskRequirements: declared with
        the task, or extracted once by the shared Task Reader. Every
        required capability must be in the agent's verified manifest, and
        needs no catalogue ID covers ("unsupported") can never be met.
        (Requirement 2.4: skip the task and record the capability gap.)
        """
        source = getattr(requirements.source, "value", requirements.source)
        if source == "unspecified":
            return PrecheckResult([RejectionReason.REQUIREMENTS_UNSPECIFIED], [])
        if source == "unreadable":
            return PrecheckResult([RejectionReason.REQUIREMENTS_UNREADABLE], [])

        verified = set(verified_capabilities)
        gap = [c for c in requirements.required_capabilities if c not in verified]
        gap += list(requirements.unsupported_requirements)
        if gap:
            return PrecheckResult([RejectionReason.CAPABILITY_MISSING], gap)
        return PrecheckResult([], [])

    def review_model_decision(self, decision) -> list[RejectionReason]:
        """Check one validated model decision against the hard rules.

        Both must agree for a task to stay eligible: the model must say
        ``suitable`` AND the score must reach the threshold (>= 0.7, as in
        the Team 3C design). A contradictory answer (suitable=True with a
        low score, or suitable=False with a high score) is rejected.
        """
        reasons: list[RejectionReason] = []
        if not decision.suitable:
            reasons.append(RejectionReason.MODEL_NOT_SUITABLE)
        if decision.match_score < self.min_match_score:
            reasons.append(RejectionReason.SCORE_BELOW_THRESHOLD)
        return reasons

    @staticmethod
    def free_slots(active_task_count: int, max_concurrent_tasks: int) -> int:
        """How many more tasks this agent may pursue right now."""
        return max(0, max_concurrent_tasks - active_task_count)
