"""Job control helpers for cataloging tasks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ...database.models import (
    AgentRun,
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    OperationRun,
)

TERMINAL_RUN_STATUSES = {"completed", "completed_with_warnings", "skipped_by_user"}
TERMINAL_AGENT_RUN_STATUSES = {"completed", "failed", "cancelled"}
OPERATION_STATUS_BY_JOB_STATUS = {
    "queued": "queued",
    "running": "running",
    "waiting_confirmation": "waiting_user",
    "paused": "paused",
    "paused_on_failure": "paused",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _operation_projection_needs_refresh(job: CatalogingJob, operation: OperationRun) -> bool:
    expected_status = OPERATION_STATUS_BY_JOB_STATUS.get(job.status)
    if not expected_status:
        return False
    completed = int(job.completed_chapters or 0)
    total = int(job.total_chapters or 0)
    if operation.status != expected_status:
        return True
    if operation.progress_current != completed or operation.progress_total != total:
        return True
    if expected_status in {"completed", "failed", "cancelled"} and operation.completed_at is None:
        return True
    if expected_status in {"waiting_user", "paused"} and not operation.attention_json:
        return True
    return (
        expected_status == "completed"
        and (operation.result_json or {}).get("outcome") != "completed_with_tools"
    )


def reconcile_cataloging_operation_projections(db: Session) -> int:
    """Repair task-centre rows that drifted from their authoritative jobs.

    Older local-CLI completion paths could commit ``CatalogingJob`` before the
    corresponding ``OperationRun`` update.  Run this before generic startup
    interruption recovery so an already-completed job is never mislabeled as
    interrupted merely because its projection was stale.
    """

    pairs = (
        db.query(CatalogingJob, OperationRun)
        .join(OperationRun, OperationRun.id == CatalogingJob.operation_id)
        .all()
    )
    repaired = 0
    for job, operation in pairs:
        if not _operation_projection_needs_refresh(job, operation):
            continue
        refresh_job_progress(db, job)
        repaired += 1
    return repaired


def refresh_job_progress(db: Session, job: CatalogingJob) -> None:
    previous_completed = int(job.completed_chapters or 0)
    db.flush()
    job.completed_chapters = (
        db.query(CatalogingChapterRun)
        .filter(
            CatalogingChapterRun.job_id == job.id,
            CatalogingChapterRun.status.in_(["completed", "completed_with_warnings"]),
        )
        .count()
    )
    job.failed_chapters = (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id, CatalogingChapterRun.status == "failed")
        .count()
    )
    job.updated_at = datetime.utcnow()
    if job.status == "completed":
        completed_runs = db.query(CatalogingChapterRun).filter(
            CatalogingChapterRun.job_id == job.id,
            CatalogingChapterRun.status.in_(["completed", "completed_with_warnings"]),
        ).all()
        for run in completed_runs:
            chapter = db.query(Chapter).filter(
                Chapter.id == run.chapter_id,
                Chapter.project_id == job.project_id,
            ).first()
            if (
                chapter is not None
                and run.chapter_version is not None
                and int(run.chapter_version) == int(chapter.current_version or 1)
            ):
                chapter.cataloging_required = False
    if job.operation_id:
        from ..operation_runtime import update_operation

        operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
        if operation:
            completed = int(job.completed_chapters or 0)
            lifecycle = OPERATION_STATUS_BY_JOB_STATUS.get(job.status, operation.status)
            previous_lifecycle = operation.status
            attention: dict = {}
            result = None
            outcome = None
            if lifecycle == "waiting_user":
                attention = {
                    "kind": "confirmation",
                    "title": "建档候选等待确认",
                    "message": job.error or "请审阅当前章节的建档候选后继续。",
                    "action_label": "查看建档候选",
                    "action_url": f"/project/{job.project_id}?view=cataloging",
                    "blocking": True,
                }
                result = {
                    "summary": f"已完成 {completed}/{job.total_chapters or 0} 章，当前章节等待确认",
                    "completed": [f"{completed} 章已完成"],
                    "incomplete": ["当前章节候选尚未确认"],
                }
                outcome = "waiting_user"
            elif lifecycle == "paused":
                attention = {
                    "kind": "cataloging_recovery",
                    "title": "作品建档已暂停",
                    "message": job.error or "请打开作品建档页处理当前章节后继续。",
                    "action_label": "前往处理建档",
                    "action_url": f"/project/{job.project_id}?view=cataloging",
                    "blocking": True,
                }
                result = {
                    "summary": job.error
                    or f"作品建档暂停在 {completed}/{job.total_chapters or 0} 章",
                    "completed": [f"{completed} 章已完成"] if completed else [],
                    "incomplete": [job.error or "当前章节尚未完成"],
                }
                outcome = "blocked"
            elif lifecycle == "completed":
                result = {
                    "summary": f"作品建档完成，共处理 {completed} 章",
                    "completed": [f"{completed} 章已完成"],
                    "incomplete": [],
                }
                outcome = "completed_with_tools"
            elif lifecycle == "failed":
                result = {
                    "summary": job.error or "作品建档失败",
                    "completed": [f"{completed} 章已完成"] if completed else [],
                    "incomplete": [job.error or "剩余章节未完成"],
                }
                outcome = "partial_success" if completed else "failed"
            elif lifecycle == "cancelled":
                result = {
                    "summary": "作品建档已取消",
                    "completed": [f"{completed} 章已完成"] if completed else [],
                    "incomplete": ["任务已取消，剩余章节未处理"],
                }
                outcome = "cancelled"
            status_changed = lifecycle != previous_lifecycle
            update_operation(
                db,
                operation,
                status=lifecycle,
                health_status="active",
                phase="cataloging",
                message=f"作品建档：已完成 {completed}/{job.total_chapters or 0} 章",
                progress_mode="determinate",
                progress_current=completed,
                progress_total=int(job.total_chapters or 0),
                checkpoint=completed > previous_completed,
                event_type=(
                    lifecycle
                    if status_changed
                    else "checkpoint"
                    if completed > previous_completed
                    else None
                ),
                payload={
                    "completed_chapters": completed,
                    "total_chapters": int(job.total_chapters or 0),
                },
                next_action=job.error or "",
                attention=attention,
                result=result,
                outcome=outcome,
                activity=status_changed,
            )


def complete_cataloging_job(db: Session, job: CatalogingJob) -> None:
    """Complete the authoritative job and its durable UI/Agent projections.

    Callers own the transaction.  Keeping all three records in the same unit
    of work prevents REST, workspace/MCP, and managed CLI completion from
    drifting when a helper commits one sidecar before the others.
    """

    completed_at = job.completed_at or datetime.utcnow()
    job.status = "completed"
    job.current_chapter_id = None
    job.blocked_chapter_id = None
    job.error = None
    job.completed_at = completed_at
    job.updated_at = completed_at

    if job.agent_run_id:
        agent_run = db.query(AgentRun).filter(AgentRun.id == job.agent_run_id).first()
        if agent_run and agent_run.status not in TERMINAL_AGENT_RUN_STATUSES:
            agent_run.status = "completed"
            agent_run.current_step = "作品建档完成"
            agent_run.summary = "作品建档完成"
            agent_run.updated_at = completed_at
            agent_run.completed_at = completed_at

    refresh_job_progress(db, job)


def first_blocking_run(db: Session, job: CatalogingJob) -> CatalogingChapterRun | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .filter(CatalogingChapterRun.status.in_(["failed", "awaiting_confirmation"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def first_retryable_run(db: Session, job: CatalogingJob) -> CatalogingChapterRun | None:
    """Return the current unit accepted by every full-retry transport.

    A paused candidate turn remains ``facts_saved``.  Treating only failures
    and confirmation waits as retryable made REST reject the same unit that
    the workspace/MCP tool could reset, and forced users to preserve facts
    they had explicitly chosen to discard.
    """

    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .filter(CatalogingChapterRun.status.in_([
            "failed",
            "awaiting_confirmation",
            "facts_saved",
        ]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def reset_run_for_retry(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> None:
    candidate_ids = [
        row.id
        for row in db.query(CatalogingCandidate.id)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .all()
    ]
    if candidate_ids:
        db.query(CatalogingApplyLog).filter(
            CatalogingApplyLog.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        db.query(CatalogingCandidate).filter(CatalogingCandidate.id.in_(candidate_ids)).delete(
            synchronize_session=False
        )
    db.query(CatalogingFact).filter(CatalogingFact.chapter_run_id == run.id).delete(
        synchronize_session=False
    )
    run.status = "pending"
    run.started_at = None
    run.completed_at = None
    run.error = None
    run.review_warning = None
    run.raw_output = None
    job.status = "running"
    job.completed_at = None
    job.current_chapter_id = run.chapter_id
    job.blocked_chapter_id = None
    job.error = None
    refresh_job_progress(db, job)


def reset_run_for_resolution_retry(
    db: Session, job: CatalogingJob, run: CatalogingChapterRun
) -> None:
    from .fact_store import clear_derived_facts_for_run

    candidate_ids = [
        row.id
        for row in db.query(CatalogingCandidate.id)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .all()
    ]
    if candidate_ids:
        db.query(CatalogingApplyLog).filter(
            CatalogingApplyLog.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        db.query(CatalogingCandidate).filter(CatalogingCandidate.id.in_(candidate_ids)).delete(
            synchronize_session=False
        )
    clear_derived_facts_for_run(db, run)
    run.status = "facts_saved"
    run.completed_at = None
    run.error = None
    run.review_warning = None
    job.status = "running"
    job.completed_at = None
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
    run.review_warning = None
    job.status = "running"
    job.blocked_chapter_id = None
    job.context_integrity = "skipped_chapter"
    job.error = None
    refresh_job_progress(db, job)
