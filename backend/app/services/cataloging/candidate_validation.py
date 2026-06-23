"""Validation helpers for deciding when a cataloging chapter is writable."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CandidateCoverage:
    total: int
    has_chapter_summary: bool
    has_chapter_outline: bool

    @property
    def is_complete(self) -> bool:
        return self.has_chapter_summary and self.has_chapter_outline

    @property
    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.has_chapter_summary:
            missing.append("chapter_summary")
        if not self.has_chapter_outline:
            missing.append("chapter-level outline")
        return missing


def _payload(candidate: Any) -> dict[str, Any]:
    raw = getattr(candidate, "edited_payload", None) or getattr(candidate, "raw_payload", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def inspect_candidate_coverage(candidates: Iterable[Any]) -> CandidateCoverage:
    items = list(candidates)
    has_summary = False
    has_chapter_outline = False
    for candidate in items:
        if getattr(candidate, "status", None) == "rejected":
            continue
        item_type = str(getattr(candidate, "item_type", "") or "")
        if item_type == "chapter_summary":
            has_summary = True
        if item_type in {"outline_create", "outline_update"}:
            node_type = str(_payload(candidate).get("node_type") or "chapter").strip().lower()
            if node_type == "chapter":
                has_chapter_outline = True
    return CandidateCoverage(
        total=len(items),
        has_chapter_summary=has_summary,
        has_chapter_outline=has_chapter_outline,
    )
