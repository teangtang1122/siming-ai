"""Apply cataloging candidates to project data.

This module is intentionally a small dispatcher. Domain-specific writes live in
the sibling *_ops modules so the cataloging pipeline does not grow into a large
single-file service.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
)
from ...database.write_coordination import DatabaseWriteLockTimeout
from ..narrative_governance import record_chapter_governance_review
from ..story_granularity import inspect_candidate_coverage_items
from .candidate_io import candidate_payload, candidate_to_dict
from .chapter_link_ops import apply_chapter_link
from .chapter_ops import apply_chapter_summary
from .character_merge_ops import apply_character_merge_candidate
from .character_ops import (
    apply_character_create,
    apply_character_relationship,
    apply_character_state,
    apply_character_timeline,
    apply_character_update,
)
from .constants import APPLY_ORDER
from .outline_ops import apply_outline
from .reconciliation import (
    prepare_reconciled_payload,
    reconcile_successful_run,
)
from .worldbuilding_ops import apply_worldbuilding, apply_worldbuilding_timeline

ApplyHandler = Callable[[Session, CatalogingCandidate, Chapter, dict[str, Any]], dict[str, Any]]


def apply_candidates_for_run(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> list[dict[str, Any]]:
    candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .filter(CatalogingCandidate.status.notin_(["rejected", "applied"]))
        .all()
    )
    candidates.sort(key=_candidate_apply_sort_key)

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
        except DatabaseWriteLockTimeout:
            raise
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
    applied_candidates = [candidate for candidate in candidates if candidate.status == "applied"]
    coverage = inspect_candidate_coverage_items(applied_candidates)
    if coverage.narrative_assessed:
        source = coverage.governance_review_source or "provided"
        confidence = {
            "llm": 0.8,
            "provided": 0.7,
            "fallback": 0.55,
        }.get(source, 0.6)
        record_chapter_governance_review(
            db,
            job.project_id,
            run.chapter,
            source=source,
            findings_count=coverage.governance_findings_count,
            confidence=confidence,
            evidence=(
                f"作品建档已汇总检查本章叙事状态；发现 {coverage.governance_findings_count} 条治理线索。"
                if coverage.governance_findings_count
                else "作品建档已显式检查本章叙事状态，本版本未产生结构化治理线索。"
            ),
        )
    # Reconciliation is part of applying a complete chapter projection.  Keep
    # the public event stream backward-compatible: callers expect one event per
    # candidate and do not need an extra synthetic candidate event here.
    reconcile_successful_run(db, run)
    return events


def _candidate_apply_sort_key(candidate: CatalogingCandidate) -> tuple[Any, ...]:
    outline_rank = 0
    if candidate.item_type in {"outline_create", "outline_update"}:
        node_type = str(candidate_payload(candidate).get("node_type") or "chapter").strip().lower()
        outline_rank = {"volume": 0, "chapter": 1, "section": 2, "scene": 2}.get(node_type, 1)
    return (
        APPLY_ORDER.get(candidate.item_type, 999),
        outline_rank,
        candidate.sort_order or 0,
        candidate.created_at,
    )


def apply_candidate(db: Session, candidate: CatalogingCandidate) -> dict[str, Any]:
    payload = prepare_reconciled_payload(db, candidate, candidate_payload(candidate))
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
