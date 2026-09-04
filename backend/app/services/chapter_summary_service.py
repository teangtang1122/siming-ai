"""Authoritative chapter-summary persistence shared by cataloging and author edits."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..core.exceptions import ConflictError, NotFoundError
from ..database.models import Chapter, ChapterSummary


def upsert_chapter_summary_record(
    db: Session,
    chapter: Chapter,
    *,
    summary_text: str,
    key_events: list[str],
    source: str,
) -> tuple[ChapterSummary, dict[str, str | None] | None]:
    """Write the one authoritative summary row without changing chapter body version."""

    normalized_summary = str(summary_text or "").strip()
    if not normalized_summary:
        raise ValueError("章节摘要为空")
    normalized_events = [str(item).strip() for item in key_events if str(item).strip()]
    old = None
    summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).first()
    if summary is None:
        summary = ChapterSummary(chapter_id=chapter.id, summary_text=normalized_summary)
        db.add(summary)
    else:
        old = {"summary_text": summary.summary_text, "key_events": summary.key_events}
        summary.summary_text = normalized_summary
    summary.key_events = json.dumps(normalized_events, ensure_ascii=False)
    summary.ai_model = str(source or "unknown")[:100]
    summary.updated_at = datetime.utcnow()
    db.flush()
    return summary, old


def update_author_chapter_summary(
    db: Session,
    project_id: str,
    chapter_id: str,
    *,
    expected_version: int,
    summary_text: str,
    key_events: list[str],
) -> dict:
    """Validate and persist an author correction without changing body version."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.id == chapter_id)
        .first()
    )
    if chapter is None:
        raise NotFoundError("章节不存在")
    current_version = int(chapter.current_version or 1)
    if expected_version != current_version:
        raise ConflictError(
            f"章节已从 v{expected_version} 更新为 v{current_version}；"
            "请重新核对正文后再修正摘要"
        )
    summary, _old = upsert_chapter_summary_record(
        db,
        chapter,
        summary_text=summary_text,
        key_events=key_events,
        source="author",
    )
    return {
        "id": summary.id,
        "chapter_id": chapter_id,
        "chapter_version": current_version,
        "summary_text": summary.summary_text,
        "key_events": key_events,
        "source": summary.ai_model,
    }


__all__ = ["update_author_chapter_summary", "upsert_chapter_summary_record"]
