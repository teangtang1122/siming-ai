"""Shared helpers for chapter-derived cataloging rollback."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.modules.story.infrastructure.entities import Chapter, OutlineNode


def json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def same_projection(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def ordered_project_chapters(db: Session, project_id: str) -> list[Chapter]:
    return (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
        .all()
    )


def delete_rows(rows: list[Any]) -> int:
    count = 0
    for row in rows:
        session = Session.object_session(row)
        if session is not None:
            session.delete(row)
            count += 1
    return count


def reset_chapter_outline_projection(node: OutlineNode) -> None:
    """Keep a chapter's structural slot while invalidating its old projection."""

    node.summary = node.planned_summary or ""
    node.actual_summary = None
    node.status = "pending"
    node.source_chapter_id = None
    node.cataloging_status = None


__all__ = [
    "delete_rows",
    "json_value",
    "ordered_project_chapters",
    "reset_chapter_outline_projection",
    "same_projection",
]
