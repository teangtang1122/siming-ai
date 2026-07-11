"""Apply cataloging candidates to project data.

This module is intentionally a small dispatcher. Domain-specific writes live in
the sibling *_ops modules so the cataloging pipeline does not grow into a large
single-file service.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from ...database.models import CatalogingApplyLog, CatalogingCandidate, CatalogingChapterRun, CatalogingJob, Chapter
from .candidate_io import candidate_payload, candidate_to_dict
from .chapter_link_ops import apply_chapter_link
from .chapter_ops import apply_chapter_summary
from .character_ops import (
    apply_character_create,
    apply_character_relationship,
    apply_character_state,
    apply_character_timeline,
    apply_character_update,
)
from .character_merge_ops import apply_character_merge_candidate
from .constants import APPLY_ORDER
from .outline_ops import apply_outline
from .worldbuilding_ops import apply_worldbuilding, apply_worldbuilding_timeline


ApplyHandler = Callable[[Session, CatalogingCandidate, Chapter, dict[str, Any]], dict[str, Any]]


def apply_candidates_for_run(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> list[dict[str, Any]]:
    candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .filter(CatalogingCandidate.status.notin_(["rejected", "applied"]))
        .all()
    )
    candidates.sort(key=lambda item: (APPLY_ORDER.get(item.item_type, 999), item.sort_order or 0, item.created_at))

    events: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate.status = "applying"
        candidate.updated_at = datetime.utcnow()
        db.flush()
        try:
            result = apply_candidate(db, candidate)
            _mark_applied(db, job, run, candidate, result)
            events.append({
                "type": "candidate_applied",
                "candidate": candidate_to_dict(candidate),
                "detail": result.get("detail"),
                "data": result,
            })
        except Exception as exc:
            candidate.status = "apply_failed"
            candidate.error = str(exc)
            events.append({
                "type": "candidate_apply_failed",
                "candidate": candidate_to_dict(candidate),
                "error": str(exc),
            })
        finally:
            candidate.updated_at = datetime.utcnow()
            db.flush()
    return events


def apply_candidate(db: Session, candidate: CatalogingCandidate) -> dict[str, Any]:
    payload = candidate_payload(candidate)
    chapter = db.query(Chapter).filter(Chapter.id == candidate.chapter_id).first()
    if not chapter:
        raise ValueError("章节不存在")

    handler = _handler_for(candidate.item_type)
    return handler(db, candidate, chapter, payload)


def _handler_for(item_type: str) -> ApplyHandler:
    handlers: dict[str, ApplyHandler] = {
        "chapter_summary": apply_chapter_summary,
        "character_create": apply_character_create,
        "character_update": apply_character_update,
        "character_state_update": apply_character_state,
        "character_timeline": apply_character_timeline,
        "character_relationship": apply_character_relationship,
        "character_merge_candidate": apply_character_merge_candidate,
        "worldbuilding_timeline": apply_worldbuilding_timeline,
        "chapter_link": apply_chapter_link,
        "outline_create": lambda db, candidate, chapter, payload: apply_outline(db, candidate, chapter, payload, True),
        "outline_update": lambda db, candidate, chapter, payload: apply_outline(db, candidate, chapter, payload, False),
        "worldbuilding_create": lambda db, candidate, chapter, payload: apply_worldbuilding(db, candidate, chapter, payload, True),
        "worldbuilding_update": lambda db, candidate, chapter, payload: apply_worldbuilding(db, candidate, chapter, payload, False),
    }
    handler = handlers.get(item_type)
    if not handler:
        raise ValueError(f"不支持的候选类型: {item_type}")
    return handler


def _mark_applied(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    candidate: CatalogingCandidate,
    result: dict[str, Any],
) -> None:
    candidate.status = "applied"
    candidate.target_type = result.get("target_type") or candidate.target_type
    candidate.target_id = result.get("target_id") or candidate.target_id
    candidate.error = None
    db.add(CatalogingApplyLog(
        job_id=job.id,
        chapter_run_id=run.id,
        candidate_id=candidate.id,
        target_type=candidate.target_type,
        target_id=candidate.target_id,
        operation=candidate.operation,
        old_value=json.dumps(result.get("old_value"), ensure_ascii=False),
        new_value=json.dumps(result.get("new_value"), ensure_ascii=False),
    ))
