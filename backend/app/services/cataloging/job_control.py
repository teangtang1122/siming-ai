"""Job control helpers for cataloging tasks."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ...database.models import CatalogingApplyLog, CatalogingCandidate, CatalogingChapterRun, CatalogingFact, CatalogingJob


TERMINAL_RUN_STATUSES = {"completed", "completed_with_warnings", "skipped_by_user"}


def refresh_job_progress(db: Session, job: CatalogingJob) -> None:
    db.flush()
    job.completed_chapters = (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id, CatalogingChapterRun.status.in_(["completed", "completed_with_warnings"]))
        .count()
    )
    job.failed_chapters = (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id, CatalogingChapterRun.status == "failed")
        .count()
    )
    job.updated_at = datetime.utcnow()


def first_blocking_run(db: Session, job: CatalogingJob) -> CatalogingChapterRun | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .filter(CatalogingChapterRun.status.in_(["failed", "awaiting_confirmation"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def reset_run_for_retry(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> None:
    candidate_ids = [
        row.id
        for row in db.query(CatalogingCandidate.id).filter(CatalogingCandidate.chapter_run_id == run.id).all()
    ]
    if candidate_ids:
        db.query(CatalogingApplyLog).filter(CatalogingApplyLog.candidate_id.in_(candidate_ids)).delete(synchronize_session=False)
        db.query(CatalogingCandidate).filter(CatalogingCandidate.id.in_(candidate_ids)).delete(synchronize_session=False)
    db.query(CatalogingFact).filter(CatalogingFact.chapter_run_id == run.id).delete(synchronize_session=False)
    run.status = "pending"
    run.started_at = None
    run.completed_at = None
    run.error = None
    run.raw_output = None
    job.status = "running"
    job.current_chapter_id = run.chapter_id
    job.blocked_chapter_id = None
    job.error = None
    refresh_job_progress(db, job)


def reset_run_for_resolution_retry(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> None:
    candidate_ids = [
        row.id
        for row in db.query(CatalogingCandidate.id).filter(CatalogingCandidate.chapter_run_id == run.id).all()
    ]
    if candidate_ids:
        db.query(CatalogingApplyLog).filter(CatalogingApplyLog.candidate_id.in_(candidate_ids)).delete(synchronize_session=False)
        db.query(CatalogingCandidate).filter(CatalogingCandidate.id.in_(candidate_ids)).delete(synchronize_session=False)
    run.status = "pending"
    run.completed_at = None
    run.error = None
    job.status = "running"
    job.current_chapter_id = run.chapter_id
    job.blocked_chapter_id = None
    job.error = None
    refresh_job_progress(db, job)


def cancel_job(job: CatalogingJob) -> None:
    job.status = "cancelled"
    job.current_chapter_id = None
    job.blocked_chapter_id = None
    job.error = None
    job.completed_at = datetime.utcnow()
    job.updated_at = datetime.utcnow()


def pause_job(job: CatalogingJob) -> None:
    job.status = "paused"
    job.updated_at = datetime.utcnow()


def resume_job(job: CatalogingJob) -> None:
    job.status = "running"
    job.error = None
    job.completed_at = None
    job.updated_at = datetime.utcnow()


def mark_run_skipped(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> None:
    run.status = "skipped_by_user"
    run.completed_at = datetime.utcnow()
    run.error = None
    job.status = "running"
    job.blocked_chapter_id = None
    job.context_integrity = "skipped_chapter"
    job.error = None
    refresh_job_progress(db, job)
