"""Shared record lookup and formatting for governance and checkpoints."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..database.models import Chapter


def _clean(value: Any, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _serialize(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _chapter(db: Session, project_id: str, chapter_id: str | None) -> Chapter | None:
    if not chapter_id:
        return None
    return (
        db.query(Chapter).filter(Chapter.project_id == project_id, Chapter.id == chapter_id).first()
    )
