"""Character background merge and compaction helpers."""
from __future__ import annotations

import re
from typing import Any

from ...database.models import Chapter


DEFAULT_BACKGROUND_LIMIT = 3000

_SPLIT_RE = re.compile(r"[\n\r]+|(?<=[。！？!?；;])")
_CHAPTER_PREFIX_RE = re.compile(r"^《[^》]{1,80}》[:：]\s*")
_SPACE_RE = re.compile(r"\s+")

_IMPORTANT_TERMS = (
    "身份",
    "出身",
    "曾",
    "曾经",
    "作为",
    "担任",
    "化名",
    "马甲",
    "伪装",
    "真实",
    "幕后",
    "传承",
    "血脉",
    "家族",
    "宗门",
    "师承",
    "目标",
    "动机",
    "冲突",
    "死亡",
    "失联",
    "背叛",
    "封印",
)


def merge_background(existing: Any, incoming: Any, chapter: Chapter, *, limit: int = DEFAULT_BACKGROUND_LIMIT) -> str | None:
    """Merge new background facts into a compact role-history profile.

    Cataloging candidates may provide either a full rewritten background or a
    small delta. This helper keeps the useful facts, removes duplicate fragments,
    and avoids repeatedly appending chapter-prefixed log entries to the profile.
    Per-chapter actions belong in CharacterTimeline; background should stay a
    durable summary of identity, origin, major history, motives, and conflicts.
    """
    old_text = _clean_text(existing)
    new_text = _clean_text(incoming)
    if not new_text:
        return old_text[:limit] or None
    if not old_text:
        return _clip(_compact_text(new_text, limit), limit)

    if _same_or_superset(old_text, new_text):
        return _clip(old_text, limit)
    if _same_or_superset(new_text, old_text):
        return _clip(new_text, limit)

    old_fragments = _split_fragments(old_text)
    new_fragments = _split_fragments(new_text)
    fragments = _dedupe_fragments(old_fragments + new_fragments)
    compacted = _compose_fragments(fragments, new_fragments, limit)
    return compacted or None


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\u3000", " ")
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def _compact_text(text: str, limit: int) -> str:
    fragments = _dedupe_fragments(_split_fragments(text))
    return _compose_fragments(fragments, fragments, limit)


def _split_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for part in _SPLIT_RE.split(text):
        item = _CHAPTER_PREFIX_RE.sub("", part).strip(" \t\r\n-•；;。")
        if item:
            fragments.append(item)
    return fragments


def _dedupe_fragments(fragments: list[str]) -> list[str]:
    result: list[str] = []
    for fragment in fragments:
        normalized = _normalize(fragment)
        if not normalized:
            continue

        existing_keys = [_normalize(item) for item in result]
        if normalized in existing_keys:
            continue

        # A rewritten cumulative profile often folds several older semicolon
        # fragments into one sentence.  Replacing only the first contained
        # fragment leaves the other fragments behind and duplicates the same
        # facts.  Collapse every exact normalized fragment contained by the
        # rewrite, preserving the earliest position.  This is deterministic
        # text compaction; it does not infer semantic similarity.
        contained = [
            index
            for index, key in enumerate(existing_keys)
            if key and key in normalized
        ]
        if contained:
            insert_at = contained[0]
            result = [
                item for index, item in enumerate(result) if index not in contained
            ]
            result.insert(insert_at, fragment)
            continue

        if any(normalized in key for key in existing_keys if key):
            continue
        result.append(fragment)
    return result


def _compose_fragments(fragments: list[str], new_fragments: list[str], limit: int) -> str:
    if not fragments:
        return ""
    ordered = sorted(
        enumerate(fragments),
        key=lambda pair: _score_fragment(pair[0], pair[1], new_fragments),
        reverse=True,
    )
    selected_indices: list[int] = []
    total = 0
    for index, fragment in ordered:
        cost = len(fragment) + 2
        if selected_indices and total + cost > limit:
            continue
        selected_indices.append(index)
        total += cost
        if total >= limit:
            break
    selected_indices.sort()
    return _clip("；".join(fragments[index] for index in selected_indices), limit)


def _score_fragment(index: int, fragment: str, new_fragments: list[str]) -> tuple[int, int, int]:
    normalized = _normalize(fragment)
    is_new = any(_normalize(item) == normalized for item in new_fragments)
    important = sum(1 for term in _IMPORTANT_TERMS if term in fragment)
    return (2 if is_new else 0, important, index)


def _same_or_superset(candidate: str, target: str) -> bool:
    candidate_key = _normalize(candidate)
    target_key = _normalize(target)
    return bool(candidate_key and target_key and target_key in candidate_key)


def _normalize(value: str) -> str:
    text = _CHAPTER_PREFIX_RE.sub("", value)
    return re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip("；;，,。 ") + "…"
