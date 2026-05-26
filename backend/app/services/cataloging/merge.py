"""Small merge helpers for cataloging writes."""
from __future__ import annotations

import json
from typing import Any

from ...database.models import Chapter


def merge_text(existing: Any, incoming: Any, chapter: Chapter, *, limit: int = 8000) -> str | None:
    new_text = str(incoming or "").strip()
    old_text = str(existing or "").strip()
    if not new_text:
        return old_text[:limit] or None
    if not old_text:
        return new_text[:limit]
    if new_text in old_text:
        return old_text[:limit]
    if old_text in new_text:
        return new_text[:limit]
    merged = f"{old_text}\n\n《{chapter.title}》：{new_text}"
    return merged[:limit]


def merge_short_text(existing: Any, incoming: Any, chapter: Chapter, *, limit: int = 4000) -> str | None:
    return merge_text(existing, incoming, chapter, limit=limit)


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
