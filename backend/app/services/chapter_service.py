"""Chapter business logic — snapshots, diffs, word tracking, and data formatting."""
from __future__ import annotations

import difflib
import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..core.utils import count_words
from ..database.models import Chapter, ChapterSnapshot, WritingLog
from ..schemas.chapter import ChapterDetail, ChapterListItem, ChapterSnapshotItem


def apply_today_word_delta(db: Session, project_id: str, delta: int) -> None:
    if delta == 0:
        return
    today = date.today()
    log = (
        db.query(WritingLog)
        .filter(WritingLog.project_id == project_id, WritingLog.date == today)
        .first()
    )
    if not log:
        log = WritingLog(project_id=project_id, date=today, total_words=0)
        db.add(log)
    log.total_words = max(0, (log.total_words or 0) + delta)


def chapter_to_list_item(chapter: Chapter, outline_context: dict) -> dict:
    outline_node = outline_context["nodes"].get(chapter.outline_node_id)
    summary = chapter.summary
    key_events: list[str] = []
    if summary and summary.key_events:
        try:
            parsed = json.loads(summary.key_events)
            key_events = parsed if isinstance(parsed, list) else []
        except Exception:
            key_events = []
    data = ChapterListItem(
        id=chapter.id,
        project_id=chapter.project_id,
        outline_node_id=chapter.outline_node_id,
        title=chapter.title,
        word_count=chapter.word_count or 0,
        current_version=chapter.current_version or 1,
        outline_title=outline_node.title if outline_node else None,
        outline_status=outline_node.status if outline_node else None,
        outline_node_type=outline_node.node_type if outline_node else None,
        outline_path=outline_context["path_for"](chapter.outline_node_id),
        summary_text=summary.summary_text if summary else None,
        key_events=[str(item) for item in key_events[:12]],
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )
    return data.model_dump(mode="json")


def chapter_to_detail(chapter: Chapter, outline_context: dict) -> dict:
    base = chapter_to_list_item(chapter, outline_context)
    data = ChapterDetail(
        **base,
        content=chapter.content or "",
        snapshot_count=len(chapter.snapshots),
    )
    return data.model_dump(mode="json")


def snapshot_to_item(snapshot: ChapterSnapshot) -> dict:
    return ChapterSnapshotItem.model_validate(snapshot).model_dump(mode="json")


def create_snapshot(chapter: Chapter, trigger_type: str) -> ChapterSnapshot:
    return ChapterSnapshot(
        chapter_id=chapter.id,
        version_number=chapter.current_version or 1,
        content=chapter.content or "",
        word_count=chapter.word_count or 0,
        trigger_type=trigger_type,
    )


def diff_snapshots(from_snapshot: ChapterSnapshot, to_snapshot: ChapterSnapshot) -> dict:
    from_lines = (from_snapshot.content or "").splitlines()
    to_lines = (to_snapshot.content or "").splitlines()
    matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines)

    changes: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changes.append(
            {
                "type": tag,
                "from_start": i1,
                "from_end": i2,
                "to_start": j1,
                "to_end": j2,
                "from_lines": from_lines[i1:i2],
                "to_lines": to_lines[j1:j2],
            }
        )

    return {
        "from_snapshot": snapshot_to_item(from_snapshot),
        "to_snapshot": snapshot_to_item(to_snapshot),
        "changes": changes,
        "total_changes": len([item for item in changes if item["type"] != "equal"]),
    }
