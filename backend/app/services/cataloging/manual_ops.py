"""Manual repair helpers for cataloging jobs."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingJob
from ...modules.story.application.content_sync import enqueue_project_sync
from .applier import apply_candidates_for_run
from .candidate_io import candidate_has_usable_summary, float_or_none
from .candidate_store import ensure_outline_section_scene_number
from .candidate_validation import (
    candidate_coverage_error_message,
    candidate_coverage_review_message,
    inspect_candidate_coverage,
)
from .constants import VALID_ITEM_TYPES
from .job_control import complete_cataloging_job, refresh_job_progress


def create_manual_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    item_type: str,
    payload: dict[str, Any],
    status: str,
    target_name: str | None = None,
    confidence: float | None = None,
    evidence: str | None = None,
) -> CatalogingCandidate:
    if item_type not in VALID_ITEM_TYPES:
        raise ValueError(f"Unsupported cataloging item type: {item_type}")
    normalized = {
        "item_type": item_type,
        "payload": dict(payload),
        "target_name": target_name,
    }
    ensure_outline_section_scene_number(db, run, normalized)
    payload = normalized["payload"]
    sort_order = db.query(CatalogingCandidate).filter(CatalogingCandidate.chapter_run_id == run.id).count()
    candidate = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=job.project_id,
        chapter_id=run.chapter_id,
        item_type=item_type,
        operation="upsert",
        target_name=(target_name or "")[:200] or None,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        edited_payload=json.dumps(payload, ensure_ascii=False),
        status=status,
        confidence=float_or_none(confidence),
        evidence=(evidence or "")[:2000] or None,
        sort_order=sort_order,
        source_task="manual_repair",
    )
    db.add(candidate)
    db.flush()
    return candidate


def has_usable_chapter_summary(db: Session, run: CatalogingChapterRun) -> bool:
    candidates = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.item_type == "chapter_summary",
            CatalogingCandidate.status.notin_(["rejected"]),
        )
        .all()
    )
    return any(candidate_has_usable_summary(candidate) for candidate in candidates)


def candidate_coverage_for_run(db: Session, run: CatalogingChapterRun):
    """Return the same completeness verdict used by automatic cataloging."""

    candidates = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status != "rejected",
        )
        .all()
    )
    return inspect_candidate_coverage(candidates, db=db, project_id=run.project_id)


def _append_review_warning(run: CatalogingChapterRun, warning: str) -> None:
    text = str(warning or "").strip()
    if not text or text in str(run.review_warning or ""):
        return
    run.review_warning = "；".join(
        value
        for value in (str(run.review_warning or "").strip("； "), text)
        if value
    )[:4000]


def apply_pending_cataloging_run(
    db: Session,
    job: CatalogingJob,
) -> tuple[CatalogingChapterRun, list[dict[str, Any]]]:
    """Apply the next confirmed chapter through one transport-neutral path."""

    run = (
        db.query(CatalogingChapterRun)
        .filter(
            CatalogingChapterRun.job_id == job.id,
            CatalogingChapterRun.status == "awaiting_confirmation",
        )
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )
    if run is None:
        raise ValueError("当前没有等待确认的章节")

    coverage = candidate_coverage_for_run(db, run)
    if not coverage.is_complete:
        raise ValueError(candidate_coverage_error_message(coverage))
    _append_review_warning(run, candidate_coverage_review_message(coverage))

    events = apply_candidates_for_run(db, job, run)
    applied_candidates = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status == "applied",
        )
        .all()
    )
    applied_coverage = inspect_candidate_coverage(
        applied_candidates,
        db=db,
        project_id=run.project_id,
    )
    has_failed = any(event.get("type") == "candidate_apply_failed" for event in events)
    now = datetime.utcnow()
    run.completed_at = now

    job.current_chapter_id = None
    job.blocked_chapter_id = None
    job.error = None

    if not applied_coverage.is_complete:
        run.status = "failed"
        run.error = candidate_coverage_error_message(
            applied_coverage,
            prefix="关键候选未完成写入",
        )
        job.status = "paused_on_failure"
        job.blocked_chapter_id = run.chapter_id
        job.error = run.error
        refresh_job_progress(db, job)
    else:
        job.last_completed_chapter_id = run.chapter_id
        run.status = (
            "completed_with_warnings"
            if has_failed or bool(run.review_warning)
            else "completed"
        )
        run.error = None
        # Production sessions intentionally disable autoflush.  Persist the
        # terminal run state before counting remaining work, otherwise the
        # just-completed run is counted as still awaiting confirmation and the
        # REST response briefly reports a false running job.
        db.flush()
        remaining_runs = (
            db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job.id)
            .filter(
                CatalogingChapterRun.status.notin_(
                    ["completed", "completed_with_warnings", "skipped_by_user"]
                )
            )
            .count()
        )
        if remaining_runs == 0:
            complete_cataloging_job(db, job)
        else:
            job.status = "running"
            job.completed_at = None
            refresh_job_progress(db, job)

    enqueue_project_sync(db, job.project_id, source="cataloging_apply")
    return run, events


def recover_failed_run_for_review(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> None:
    run.status = "awaiting_confirmation"
    run.completed_at = run.completed_at or datetime.utcnow()
    run.error = None
    run.review_warning = None
    job.status = "waiting_confirmation"
    job.blocked_chapter_id = run.chapter_id
    job.error = None
    refresh_job_progress(db, job)
