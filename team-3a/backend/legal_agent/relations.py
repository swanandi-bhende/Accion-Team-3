"""Clause- and contract-level relationship detection (Requirement 10.2).

Rule-based and deliberately conservative: an edge is only created when the text contains
an explicit cue ("subject to the termination provisions", "notwithstanding ... liability",
"supersedes all prior agreements"). It will miss relationships expressed in other words,
and it never invents one. Edges are therefore a lower bound, not a complete legal analysis.
"""
from __future__ import annotations

import re

from backend.legal_agent.models import Clause, ClauseType

# How a clause type is referred to inside other clauses.
TYPE_NAMES: dict[ClauseType, tuple[str, ...]] = {
    ClauseType.LIABILITY: ("liability", "liable"),
    ClauseType.INDEMNIFICATION: ("indemnif", "hold harmless"),
    ClauseType.TERMINATION: ("terminat",),
    ClauseType.PAYMENT_TERMS: ("payment", "fees", "invoice"),
    ClauseType.CONFIDENTIALITY: ("confidential", "non-disclosure"),
    ClauseType.DISPUTE_RESOLUTION: ("dispute", "arbitration", "governing law"),
    ClauseType.FORCE_MAJEURE: ("force majeure",),
}

DEPENDS_CUES = re.compile(
    r"\b(subject to|pursuant to|in accordance with|as set out in|as provided in|"
    r"as described in|without prejudice to)\b[^.]{0,80}",
    re.IGNORECASE,
)
CONFLICT_CUES = re.compile(
    r"\b(notwithstanding|in conflict with|conflicts? with|inconsistent with|"
    r"shall prevail over|takes? precedence over)\b[^.]{0,80}",
    re.IGNORECASE,
)
SUPERSEDES_CUE = re.compile(
    r"\b(supersedes?|replaces?|amends and restates|in lieu of)\b[^.]{0,60}"
    r"\b(prior|previous|earlier|existing|agreement|contract)\b",
    re.IGNORECASE,
)


def _mentioned_types(fragment: str, exclude: ClauseType) -> set[ClauseType]:
    lowered = fragment.lower()
    return {
        ctype
        for ctype, names in TYPE_NAMES.items()
        if ctype != exclude and any(n in lowered for n in names)
    }


def detect_clause_relationships(clauses: list[Clause]) -> list[tuple[int, int, str]]:
    """Returns (source_index, target_index, edge_type) triples between clauses of one contract.

    The target is the first clause of the referenced type; a reference to a type the
    contract does not contain produces no edge.
    """
    first_of_type: dict[ClauseType, int] = {}
    for i, clause in enumerate(clauses):
        first_of_type.setdefault(clause.clause_type, i)

    found: dict[tuple[int, int, str], None] = {}
    for i, clause in enumerate(clauses):
        for pattern, edge_type in ((DEPENDS_CUES, "depends_on"), (CONFLICT_CUES, "conflicts_with")):
            for match in pattern.finditer(clause.text):
                for target_type in _mentioned_types(match.group(0), clause.clause_type):
                    j = first_of_type.get(target_type)
                    if j is not None and j != i:
                        found[(i, j, edge_type)] = None
    return list(found)


def detect_supersedes(text: str) -> bool:
    """True when the contract says it replaces earlier agreements."""
    return bool(SUPERSEDES_CUE.search(text))
