"""Story-granularity audit, repair, and narrative-ledger tools."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....database.models import Chapter
from ....services.cataloging.launcher import create_and_queue_cataloging_job
from ....services.narrative_ledger import (
    list_narrative_ledger,
    revise_narrative_ledger_entry,
)
from ....services.story_granularity import inspect_chapter_granularity


async def get_narrative_ledger(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    def items(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return [part.strip() for part in str(value or "").split(",") if part.strip()]

    ledger = list_narrative_ledger(
        db,
        project_id,
        chapter_id=str(args.get("chapter_id") or "").strip(),
        types=items(args.get("types") or args.get("type")),
        statuses=items(args.get("statuses") or args.get("status")),
        storyline=str(args.get("storyline") or "").strip(),
    )
    return {
        "tool": "get_narrative_ledger",
        "status": "ok",
        "detail": f"Found {len(ledger)} active narrative ledger entries",
        "data": {"items": ledger, "total": len(ledger)},
    }


async def update_narrative_ledger_entry(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    entry_id = str(args.get("entry_id") or args.get("id") or "").strip()
    if not entry_id:
        return {
            "tool": "update_narrative_ledger_entry",
            "status": "skipped",
            "detail": "entry_id is required",
            "data": None,
        }
    entry = revise_narrative_ledger_entry(db, project_id, entry_id, args)
    if not entry:
        return {
            "tool": "update_narrative_ledger_entry",
            "status": "skipped",
            "detail": "Narrative ledger entry was not found",
            "data": None,
        }
    commit_session(db)
    return {
        "tool": "update_narrative_ledger_entry",
        "status": "ok",
        "detail": "Narrative ledger entry updated",
        "data": entry,
    }


async def inspect_story_granularity(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter_id = str(args.get("chapter_id") or "").strip()
    level = str(args.get("level") or "narrative").strip().lower()
    level = level if level in {"basic", "narrative"} else "narrative"
    limit = max(1, min(500, int(args.get("limit") or 200)))
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if chapter_id:
        query = query.filter(Chapter.id == chapter_id)
    chapters = query.order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc()).limit(limit).all()
    checks = [
        inspect_chapter_granularity(db, project_id, chapter, level=level)
        for chapter in chapters
    ]
    missing_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for item in checks:
        for key in item["missing"]:
            missing_counts[key] = missing_counts.get(key, 0) + 1
        for key in item["warnings"]:
            warning_counts[key] = warning_counts.get(key, 0) + 1
    return {
        "tool": "inspect_story_granularity",
        "status": "ok",
        "detail": (
            f"已审计 {len(checks)} 个章节，发现 {sum(missing_counts.values())} 个硬缺口、"
            f"{sum(warning_counts.values())} 个警告"
        ),
        "data": {
            "chapters_checked": len(checks),
            "level": level,
            "missing_counts": missing_counts,
            "warning_counts": warning_counts,
            "chapters": checks,
        },
    }


async def repair_story_granularity(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Repair archive gaps by rerunning the canonical cataloging pipeline."""

    mode = str(args.get("mode") or "manual").strip().lower()
    mode = mode if mode in {"auto", "manual"} else "manual"
    repair_level = str(args.get("repair_level") or "basic").strip().lower()
    repair_level = repair_level if repair_level in {"basic", "narrative"} else "basic"
    chapter_id = str(args.get("chapter_id") or "").strip()
    limit = max(1, min(100, int(args.get("limit") or 20)))
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if chapter_id:
        query = query.filter(Chapter.id == chapter_id)
    chapters = query.order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc()).limit(limit).all()
    target_chapters: list[Chapter] = []
    audits: list[dict[str, Any]] = []
    for chapter in chapters:
        audit = inspect_chapter_granularity(
            db,
            project_id,
            chapter,
            level=repair_level,
        )
        if audit["ok"] and not bool(args.get("force")):
            continue
        target_chapters.append(chapter)
        audits.append({
            "chapter_id": chapter.id,
            "title": chapter.title,
            "missing": audit["missing"],
            "warnings": audit["warnings"],
        })

    launch: dict[str, Any] | None = None
    if target_chapters:
        _job, launch = create_and_queue_cataloging_job(
            db,
            project_id,
            [chapter.id for chapter in target_chapters],
            execution_mode=mode,
            model_override=str(args.get("model") or "").strip() or None,
            trigger_source="granularity_repair",
            run_now=True,
        )
    return {
        "tool": "repair_story_granularity",
        "status": "ok",
        "detail": (
            f"已将 {len(target_chapters)} 个章节交给正式建档流水线修复"
            if target_chapters
            else "未发现需要修复的章节"
        ),
        "data": {
            "mode": mode,
            "repair_level": repair_level,
            "chapters": audits,
            "cataloging_job": launch,
        },
    }


__all__ = [
    "get_narrative_ledger",
    "inspect_story_granularity",
    "repair_story_granularity",
    "update_narrative_ledger_entry",
]
