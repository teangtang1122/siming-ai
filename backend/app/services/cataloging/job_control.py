"""Job control helpers for cataloging tasks."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ...database.models import CatalogingApplyLog, CatalogingCandidate, CatalogingChapterRun, CatalogingFact, CatalogingJob


TERMINAL_RUN_STATUSES = {"completed", "completed_with_warnings", "skipped_by_user"}


def refresh_job_progress(db: Session, job: CatalogingJob) -> None:
    previous_completed = int(job.completed_chapters or 0)
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
    if job.operation_id:
        from ...database.models import OperationRun
        from ..operation_runtime import update_operation

        operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
        if operation:
            completed = int(job.completed_chapters or 0)
            status_map = {
                "queued": "queued",
                "running": "running",
                "waiting_confirmation": "waiting_user",
                "paused": "paused",
                "paused_on_failure": "paused",
                "completed": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }
            update_operation(
                db,
                operation,
                status=status_map.get(job.status, operation.status),
                phase="cataloging",
                message=f"作品建档：已完成 {completed}/{job.total_chapters or 0} 章",
                progress_mode="determinate",
                progress_current=completed,
                progress_total=int(job.total_chapters or 0),
                checkpoint=completed > previous_completed,
                event_type="checkpoint" if completed > previous_completed else None,
                payload={"completed_chapters": completed, "total_chapters": int(job.total_chapters or 0)},
                next_action=job.error,
            )


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
    run.status = "facts_saved"
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
