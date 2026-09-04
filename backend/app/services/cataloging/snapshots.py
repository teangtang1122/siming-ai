"""Snapshot helpers for cataloging writes."""
from __future__ import annotations

from typing import Any

from ...database.models import Chapter, Character, OutlineNode, WorldbuildingEntry
from ..character_archive import character_archive_payload


def character_snapshot(character: Character | None) -> dict | None:
    if not character:
        return None
    payload = character_archive_payload(character)
    # Legacy snapshot readers expect state fields at the top level.  Preserve
    # that wire shape while deriving every value from the canonical read model.
    state = payload.pop("state")
    return {**payload, **state}


def worldbuilding_snapshot(entry: WorldbuildingEntry | None) -> dict | None:
    if not entry:
        return None
    return {
        "id": entry.id,
        "dimension": entry.dimension,
        "title": entry.title,
        "content": entry.content,
        "status": entry.status,
        "confidence": entry.confidence,
    }


def outline_snapshot(node: OutlineNode | None) -> dict | None:
    if not node:
        return None
    return {
        "id": node.id,
        "title": node.title,
        "node_type": node.node_type,
        "parent_id": node.parent_id,
        "summary": node.summary,
        "status": node.status,
        "source_chapter_id": node.source_chapter_id,
        "actual_summary": node.actual_summary,
        "planned_summary": node.planned_summary,
        "cataloging_status": node.cataloging_status,
    }


def chapter_change_title(chapter: Chapter, summary: Any) -> str:
    detail = str(summary or "").strip()
    if len(detail) > 80:
        detail = detail[:80] + "..."
    return f"《{chapter.title}》：{detail or '信息更新'}"
