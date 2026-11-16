"""Clause identification (Requirement 9).

`RuleBasedExtractor` is deterministic and needs no network, which keeps Property 17 and
the unit tests reproducible. `LLMClauseExtractor` implements the same
`extract(text) -> list[Clause]` signature; the agent uses it when an API key is set and
falls back to the rule-based one if the model returns nothing usable.
"""
from __future__ import annotations

import io
import re

from backend.legal_agent.models import (
    SUPPORTED_FORMATS,
    Clause,
    ClauseType,
    ParsedContract,
    UnsupportedFormatError,
    DocumentDecodeError,
)

# Signal terms per clause type. Weights are deliberately conservative — a single
# generic hit yields a low-confidence clause rather than a confident wrong answer.
CLAUSE_SIGNALS: dict[ClauseType, list[str]] = {
    ClauseType.LIABILITY: [
        "liability", "liable", "damages", "limitation of liability", "consequential damages",
    ],
    ClauseType.INDEMNIFICATION: [
        "indemnify", "indemnification", "hold harmless", "defend and hold",
    ],
    ClauseType.TERMINATION: [
        "termination", "terminate", "notice period", "for cause", "expiration of this agreement",
    ],
    ClauseType.PAYMENT_TERMS: [
        "payment", "invoice", "net 30", "fees", "compensation", "payable within",
    ],
    ClauseType.CONFIDENTIALITY: [
        "confidential", "confidentiality", "non-disclosure", "proprietary information",
    ],
    ClauseType.DISPUTE_RESOLUTION: [
        "arbitration", "dispute", "governing law", "jurisdiction", "mediation", "venue",
    ],
    ClauseType.FORCE_MAJEURE: [
        "force majeure", "act of god", "beyond the reasonable control", "natural disaster",
    ],
}

ENTITY_PATTERN = re.compile(
    r'\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)*'
    r'\s+(?:Inc|LLC|Ltd|Corp|Corporation|GmbH|PLC|LLP|Pvt)\.?)\b'
)
JURISDICTION_PATTERN = re.compile(
    r"laws of (?:the )?([A-Z][A-Za-z ]+?)(?:,|\.|;| and| shall)", re.IGNORECASE
)


def validate_format(fmt: str) -> str:
    normalized = fmt.lower().lstrip(".")
    if normalized not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(fmt)
    return normalized


def extract_text(data: bytes | str, fmt: str) -> str:
    """Converts document bytes to plain text.

    txt, pdf (pdfminer.six) and docx (python-docx) are decoded here; a str is returned
    as-is. A corrupt file raises DocumentDecodeError rather than leaking a library error.
    """
    if isinstance(data, str):
        return data
    if fmt == "txt":
        return data.decode("utf-8", errors="replace")
    elif fmt == "pdf":
        try:
            from pdfminer.high_level import extract_text as pdf_extract_text

            return pdf_extract_text(io.BytesIO(data))
        except Exception as exc:
            raise DocumentDecodeError(f"could not read PDF: {exc}") from exc
    elif fmt == "docx":
        try:
            import docx

            doc = docx.Document(io.BytesIO(data))
            # Blank line between paragraphs: split_sections() treats blank lines as clause
            # boundaries, and Word stores each paragraph separately.
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            raise DocumentDecodeError(f"could not read DOCX: {exc}") from exc
    raise NotImplementedError(
        f"binary {fmt} decoding is not wired up yet; pass pre-extracted text"
    )


def split_sections(text: str) -> list[tuple[int, int, str]]:
    """Splits on blank lines, returning (start, end, section_text) with offsets
    into the original document so Req 9.1's position metadata stays accurate."""
    sections: list[tuple[int, int, str]] = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        start = text.find(block, cursor)
        if start == -1:
            start = cursor
        end = start + len(block)
        cursor = end
        if block.strip():
            sections.append((start, end, block))
    return sections


class RuleBasedExtractor:
    def __init__(self, min_confidence: float = 0.35):
        self.min_confidence = min_confidence

    def _score(self, section: str, clause_type: ClauseType) -> float:
        lowered = section.lower()
        signals = CLAUSE_SIGNALS[clause_type]
        hits = sum(1 for term in signals if term in lowered)
        if hits == 0:
            return 0.0
        # Saturating score: one hit ~0.55, two ~0.75, three or more approaches 0.95.
        return min(0.95, 0.35 + 0.20 * hits)

    def extract(self, text: str) -> list[Clause]:
        clauses: list[Clause] = []
        for start, end, section in split_sections(text):
            best_type: ClauseType | None = None
            best_score = 0.0
            for clause_type in ClauseType:
                score = self._score(section, clause_type)
                if score > best_score:
                    best_type, best_score = clause_type, score
            if best_type is not None and best_score >= self.min_confidence:
                clauses.append(
                    Clause(
                        clause_type=best_type,
                        text=section.strip(),
                        start=start,
                        end=end,
                        confidence=round(best_score, 4),
                    )
                )
        return clauses


def extract_entities(text: str) -> list[str]:
    """Named legal entities, used for the cross-contract auto-linking in Req 10.5."""
    seen: dict[str, None] = {}
    for match in ENTITY_PATTERN.finditer(text):
        seen.setdefault(match.group(1).strip(), None)
    return list(seen)


def extract_jurisdiction(text: str) -> str | None:
    match = JURISDICTION_PATTERN.search(text)
    return match.group(1).strip() if match else None


def parse_document(contract_id: str, data: bytes | str, fmt: str) -> ParsedContract:
    fmt = validate_format(fmt)
    text = extract_text(data, fmt)
    from backend.legal_agent.relations import detect_clause_relationships, detect_supersedes

    clauses = RuleBasedExtractor().extract(text)
    return ParsedContract(
        contract_id=contract_id,
        clauses=clauses,
        entities=extract_entities(text),
        jurisdiction=extract_jurisdiction(text),
        relationships=detect_clause_relationships(clauses),
        supersedes_prior=detect_supersedes(text),
    )

class LLMClauseExtractor:
    """LLM-backed clause identification, same `extract(text) -> list[Clause]` contract as
    RuleBasedExtractor (Req 9.1-9.3).

    Provider is chosen from the environment, and a key is only ever sent to its own
    provider: ANTHROPIC_API_KEY -> Anthropic, GROQ_API_KEY -> Groq. The model name is
    configurable via LLM_MODEL. Model output is never trusted: offsets are re-derived
    from the text, confidence is clamped to [0.0, 1.0] (Property 17), and unknown clause
    types are dropped.
    """

    DEFAULT_MODELS = {"anthropic": "claude-3-5-sonnet-latest", "groq": "openai/gpt-oss-120b"}

    PROMPT = (
        "Extract the legal clauses from the contract text below. For each clause give: "
        '"clause_type" (one of: liability, indemnification, termination, payment_terms, '
        'confidentiality, dispute_resolution, force_majeure), "text" (copied exactly from '
        'the contract) and "confidence" (0.0-1.0). Respond with only a JSON object of the '
        'form {"clauses": [ ... ]}, no markdown.\n\nContract text:\n'
    )

    def __init__(self, client=None, provider: str | None = None, model: str | None = None):
        import os

        if client is None:
            if provider is None:
                provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "groq"
            if provider == "anthropic":
                import anthropic

                client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            elif provider == "groq":
                import groq

                client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])
            else:
                raise ValueError(f"unknown LLM provider {provider!r}")
        self.client = client
        self.provider = provider or "anthropic"
        self.model = model or os.environ.get("LLM_MODEL") or self.DEFAULT_MODELS.get(
            self.provider, self.DEFAULT_MODELS["anthropic"]
        )

    def _complete(self, text: str) -> str:
        prompt = self.PROMPT + text
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(getattr(b, "text", "") for b in response.content)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    @staticmethod
    def _parse_json(content: str):
        import json

        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?|```$", "", content).strip()
        data = json.loads(content)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        return data

    def extract(self, text: str) -> list[Clause]:
        try:
            items = self._parse_json(self._complete(text))
        except Exception:
            return []  # caller falls back to the rule-based extractor

        valid_types = {t.value: t for t in ClauseType}
        clauses: list[Clause] = []
        cursor = 0
        for item in items:
            try:
                ctype = valid_types.get(str(item["clause_type"]).strip().lower())
                body = str(item["text"]).strip()
                if ctype is None or not body:
                    continue
                start = text.find(body, cursor)
                if start == -1:
                    start = text.find(body)
                if start == -1:
                    continue  # model paraphrased instead of quoting; drop it
                cursor = start + len(body)
                confidence = min(1.0, max(0.0, float(item["confidence"])))
                clauses.append(
                    Clause(
                        clause_type=ctype,
                        text=body,
                        start=start,
                        end=start + len(body),
                        confidence=round(confidence, 4),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return clauses
