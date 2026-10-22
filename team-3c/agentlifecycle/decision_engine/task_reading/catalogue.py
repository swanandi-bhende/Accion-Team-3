"""The shared list of capability IDs the platform knows about.

The Task Reader may only answer with IDs from this catalogue. That gives
code an exact vocabulary to compare against each agent's verified
capability_manifest ("analyze_text" == "analyze_text"), instead of the
LLM's free wording ("text analysis").

In a full deployment the catalogue should contain every capability that
has a registered handler or remote service. For the demo it holds the one
real reference capability (WP1's analyze_text handler).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


class CapabilityCatalogue:
    def __init__(self, entries: Mapping[str, str]) -> None:
        cleaned: dict[str, str] = {}
        for capability_id, description in entries.items():
            capability_id = (capability_id or "").strip()
            description = (description or "").strip()
            if not capability_id:
                raise ValueError("Capability IDs must not be empty")
            if not description:
                raise ValueError(f"Capability {capability_id!r} needs a description")
            cleaned[capability_id] = description
        if not cleaned:
            raise ValueError("A capability catalogue needs at least one entry")
        self._entries = dict(sorted(cleaned.items()))

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._entries)

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._entries

    def for_prompt(self) -> str:
        return json.dumps(
            [{"id": cid, "description": desc} for cid, desc in self._entries.items()],
            indent=1,
        )

    @property
    def fingerprint(self) -> str:
        """Changes whenever an ID or description changes (invalidates cache)."""
        raw = json.dumps(self._entries, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]


DEMO_CAPABILITY_CATALOGUE = CapabilityCatalogue(
    {
        "analyze_text": (
            "Analyse a supplied plain-text input and return basic statistics "
            "such as word count and character count."
        ),
    }
)
