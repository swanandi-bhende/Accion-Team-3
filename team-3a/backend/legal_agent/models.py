"""Domain types for legal contract analysis (Requirements 9-11)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class ClauseType(str, Enum):
    """The seven clause types Requirement 9.2 requires the agent to identify."""

    LIABILITY = "liability"
    INDEMNIFICATION = "indemnification"
    TERMINATION = "termination"
    PAYMENT_TERMS = "payment_terms"
    CONFIDENTIALITY = "confidentiality"
    DISPUTE_RESOLUTION = "dispute_resolution"
    FORCE_MAJEURE = "force_majeure"


SUPPORTED_FORMATS = ("pdf", "docx", "txt")


class DocumentDecodeError(ValueError):
    """The file has a supported extension but its bytes cannot be decoded."""


class UnsupportedFormatError(ValueError):
    """Req 9.4: the error names the formats that are accepted."""

    def __init__(self, fmt: str):
        super().__init__(
            f"unsupported document format {fmt!r}; supported formats: "
            + ", ".join(SUPPORTED_FORMATS)
        )
        self.format = fmt
        self.supported_formats = list(SUPPORTED_FORMATS)


@dataclass
class Clause:
    clause_type: ClauseType
    text: str
    start: int
    end: int
    confidence: float

    def __post_init__(self):
        # Req 9.3 / Property 17: confidence is a probability, enforced at construction
        # so no downstream consumer has to re-check the range.
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} outside [0.0, 1.0]")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clause_type"] = self.clause_type.value
        return d


@dataclass
class ParsedContract:
    contract_id: str
    clauses: list[Clause] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    jurisdiction: str | None = None
    # (source_clause_index, target_clause_index, edge_type) — Req 10.2.
    relationships: list = field(default_factory=list)
    # True when the text says it replaces earlier agreements; the graph resolves which.
    supersedes_prior: bool = False
