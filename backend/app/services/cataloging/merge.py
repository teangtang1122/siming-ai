"""Small merge helpers for cataloging writes."""
from __future__ import annotations

import json
import re
from typing import Any

from ...database.models import Chapter


_FRAGMENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|[\r\n]+")
_CHAPTER_PREFIX_RE = re.compile(r"^《[^》]{1,200}》[:：]\s*")
_NORMALIZE_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)


def merge_text(existing: Any, incoming: Any, chapter: Chapter | None, *, limit: int = 8000) -> str | None:
    new_text = str(incoming or "").strip()
    old_text = str(existing or "").strip()
    if not new_text:
        return old_text[:limit] or None
    if not old_text:
        return new_text[:limit]
    source_title = chapter.title if chapter else "手动合并"

    # Re-cataloging a revised version of the same chapter replaces that
    # chapter's contribution.  Keeping two same-title sections makes repeated
    # cataloging visibly bloat character cards and later writing context.
    marker = f"《{source_title}》："
    start = old_text.find(marker)
    if start >= 0:
        section_start = start
        if start >= 2 and old_text[start - 2 : start] == "\n\n":
            section_start = start - 2
        next_section = old_text.find("\n\n《", start + len(marker))
        suffix = old_text[next_section:].strip() if next_section >= 0 else ""
        prefix = old_text[:section_start].rstrip()
        base_without_current_chapter = "\n\n".join(
            value for value in (prefix, suffix) if value
        )
        chapter_delta = _remove_repeated_fragments(
            base_without_current_chapter,
            _strip_chapter_prefix(new_text),
        )
        replacement = f"{marker}{chapter_delta}" if chapter_delta else ""
        merged = "\n\n".join(
            value for value in (prefix, replacement, suffix) if value
        )
        return merged[:limit]

    if new_text in old_text:
        return old_text[:limit]
    if chapter is None and old_text in new_text:
        return new_text[:limit]

    # Cataloging models often return a cumulative card rather than the chapter
    # delta requested by the contract.  Appending that full card duplicates
    # exact sentences already owned by the author or earlier chapters.  Remove
    # only deterministically identical fragments; semantic rewrites remain for
    # author review instead of being guessed at here.
    chapter_delta = _remove_repeated_fragments(old_text, new_text)
    if not chapter_delta:
        return old_text[:limit]
    merged = f"{old_text}\n\n{marker}{chapter_delta}"
    return merged[:limit]


def merge_short_text(existing: Any, incoming: Any, chapter: Chapter | None, *, limit: int = 4000) -> str | None:
    return merge_text(existing, incoming, chapter, limit=limit)


def _strip_chapter_prefix(value: str) -> str:
    return _CHAPTER_PREFIX_RE.sub("", value.strip(), count=1).strip()


def _fragment_key(value: str) -> str:
    return _NORMALIZE_RE.sub("", _strip_chapter_prefix(value).lower())


def _split_fragments(value: str) -> list[str]:
    return [
        fragment.strip()
        for fragment in _FRAGMENT_SPLIT_RE.split(value)
        if fragment.strip()
    ]


def _join_fragments(fragments: list[str]) -> str:
    merged = ""
    for fragment in fragments:
        if merged and merged[-1] not in "。！？!?；;" and fragment[0] not in "，,。！？!?；;":
            merged += "；"
        merged += fragment
    return merged.strip()


def _remove_repeated_fragments(existing: str, incoming: str) -> str:
    existing_keys = {
        key
        for fragment in _split_fragments(existing)
        if (key := _fragment_key(fragment))
    }
    seen = set(existing_keys)
    novel: list[str] = []
    for fragment in _split_fragments(incoming):
        key = _fragment_key(fragment)
        if not key or key in seen:
            continue
        seen.add(key)
        novel.append(_strip_chapter_prefix(fragment))
    return _join_fragments(novel)


def merge_json_list(existing: str | None, incoming: Any) -> str | None:
    values: list[str] = []
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, list):
                values.extend(str(item) for item in parsed if str(item).strip())
        except Exception:
            values.extend(part.strip() for part in str(existing).split("；") if part.strip())
    if isinstance(incoming, list):
        values.extend(str(item) for item in incoming if str(item).strip())
    elif incoming:
        values.append(str(incoming).strip())

    seen: set[str] = set()
    merged: list[str] = []
    for item in values:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(key)
    return json.dumps(merged, ensure_ascii=False) if merged else None
