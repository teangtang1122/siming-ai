"""Create and launch the canonical cataloging pipeline.

Author-triggered chapter cataloging and the cataloging UI enter the canonical
worker through this module.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from ...ai.local_cli_adapter import is_local_cli_provider
from ...architecture.uow import commit_session
from ...database.models import (
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    OperationRun,
)
from ...database.session import SessionLocal
from .job_control import cancel_job, refresh_job_progress
from .constants import JOB_RUNNING_STATUSES
from .local_cli_agent import (
    cancel_local_cli_cataloging_worker,
    ensure_local_cli_cataloging_worker,
)
from .model_selection import cataloging_model_selection
from .orchestrator import create_cataloging_job, job_to_dict, stream_cataloging_job


CHAPTER_SAVE_SOURCE = "chapter_save"
_LAUNCH_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
logger = logging.getLogger(__name__)


def cancel_superseded_chapter_cataloging_jobs(
    db: Session,
    project_id: str,
    chapter_ids: list[str],
) -> list[str]:
    if not chapter_ids:
        return []
    jobs = (
        db.query(CatalogingJob)
        .join(CatalogingChapterRun, CatalogingChapterRun.job_id == CatalogingJob.id)
        .filter(
            CatalogingJob.project_id == project_id,
            CatalogingJob.model_source.like(f"{CHAPTER_SAVE_SOURCE}:%"),
            CatalogingJob.status.notin_(_TERMINAL_JOB_STATUSES),
            CatalogingChapterRun.chapter_id.in_(chapter_ids),
        )
        .distinct()
        .all()
    )
    cancelled: list[str] = []
    for job in jobs:
        cancel_job(job)
        refresh_job_progress(db, job)
        if job.execution_backend == "local_cli_agent":
            cancel_local_cli_cataloging_worker(job.id, terminal=True)
        cancelled.append(job.id)
    if cancelled:
        commit_session(db)
    return cancelled


def find_blocking_chapter_cataloging_job(
    db: Session,
    project_id: str,
    *,
    allow_chapter_id: str | None = None,
) -> CatalogingJob | None:
    """Return an unfinished auto-cataloging job that fences a new chapter.

    A rewrite of the same chapter is allowed; its successful save supersedes
    that chapter's older job. Any unfinished job for another chapter remains a
    hard project-level prose fence.
    """

    query = (
        db.query(CatalogingJob)
        .join(CatalogingChapterRun, CatalogingChapterRun.job_id == CatalogingJob.id)
        .filter(
            CatalogingJob.project_id == project_id,
            CatalogingJob.model_source.like(f"{CHAPTER_SAVE_SOURCE}:%"),
            CatalogingJob.status.in_(JOB_RUNNING_STATUSES),
        )
    )
    allowed = str(allow_chapter_id or "").strip()
    if allowed:
        query = query.filter(CatalogingChapterRun.chapter_id != allowed)
    return query.order_by(CatalogingJob.updated_at.desc(), CatalogingJob.created_at.desc()).first()


def cataloging_block_result(tool: str, job: CatalogingJob) -> dict[str, Any]:
    from ..workspace.turn_control import AssistantTurnDirective, apply_turn_directive

    result = {
        "tool": tool,
        "status": "blocked",
        "detail": "上一章建档尚未处理完成，本轮未生成下一章。",
        "data": {
            "blocking_job_id": job.id,
            "operation_id": job.operation_id,
            "cataloging_status": job.status,
            "blocked_chapter_id": job.blocked_chapter_id or job.current_chapter_id,
            "allowed_actions": ["retry", "open_cataloging"],
        },
    }
    return apply_turn_directive(result, AssistantTurnDirective.BLOCKED_ON_CATALOGING)


def find_cataloging_required_chapter(
    db: Session,
    project_id: str,
    *,
    allow_chapter_id: str | None = None,
) -> Chapter | None:
    """Return a saved chapter whose current version still needs cataloging."""
    query = db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.cataloging_required.is_(True),
    )
    allowed = str(allow_chapter_id or "").strip()
    if allowed:
        query = query.filter(Chapter.id != allowed)
    return query.order_by(Chapter.updated_at.desc(), Chapter.created_at.desc()).first()


def cataloging_required_block_result(tool: str, chapter: Chapter) -> dict[str, Any]:
    from ..workspace.turn_control import AssistantTurnDirective, apply_turn_directive

    return apply_turn_directive(
        {
            "tool": tool,
            "status": "blocked",
            "detail": f"《{chapter.title}》已保存但尚未完成建档，本轮未生成下一章。",
            "data": {
                "blocked_chapter_id": chapter.id,
                "cataloging_required": True,
                "allowed_actions": ["start_cataloging", "open_cataloging"],
            },
        },
        AssistantTurnDirective.BLOCKED_ON_CATALOGING,
    )


def _pause_failed_worker(
    db: Session,
    job: CatalogingJob,
    message: str,
    failure_class: str,
) -> None:
    run = (
        db.query(CatalogingChapterRun)
        .filter(
            CatalogingChapterRun.job_id == job.id,
            CatalogingChapterRun.status.notin_(
                ["completed", "completed_with_warnings", "skipped_by_user"]
            ),
        )
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )
    if run:
        run.status = "failed"
        run.error = message
        job.blocked_chapter_id = run.chapter_id
        job.current_chapter_id = run.chapter_id
    job.status = "paused_on_failure"
    job.error = message
    refresh_job_progress(db, job)
    operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
    if operation:
        operation.failure_class = failure_class[:80]
        operation.health_status = "disconnected" if failure_class == "interrupted" else "stalled"
        operation.next_action = "请重试当前建档章节，或取消任务后重新建档"


def mark_cataloging_worker_failure(
    job_id: str,
    error: BaseException | str,
    *,
    failure_class: str,
) -> bool:
    """Persist a retryable worker failure with a fresh database session."""

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOB_STATUSES:
            return False
        message = str(error or "作品建档后台任务意外停止").strip()[:2000]
        _pause_failed_worker(db, job, message, failure_class)
        commit_session(db)
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to persist cataloging worker failure for %s", job_id)
        return False
    finally:
        db.close()


def mark_interrupted_cataloging_jobs(db: Session) -> int:
    """Convert internal workers lost across process restart into retryable jobs."""

    jobs = (
        db.query(CatalogingJob)
        .filter(
            CatalogingJob.execution_backend == "internal_llm",
            CatalogingJob.status.in_(["queued", "running"]),
        )
        .all()
    )
    for job in jobs:
        message = "司命上次关闭时章节建档仍在运行，后台 worker 已中断，请重试当前章节"
        _pause_failed_worker(db, job, message, "interrupted")
    return len(jobs)


def create_and_queue_cataloging_job(
    db: Session,
    project_id: str,
    chapter_ids: list[str],
    *,
    execution_mode: str = "auto",
    model_override: str | None = None,
    backend_override: str | None = None,
    provider_override: str | None = None,
    trigger_source: str = "manual",
    run_now: bool = True,
) -> tuple[CatalogingJob, dict[str, Any]]:
    """Create one canonical job and optionally schedule its worker."""

    backend = str(backend_override or "").strip()
    if backend == "external_agent" and not model_override:
        # An unmanaged MCP client must not cause an implicit internal-model
        # selection (or spend).  The durable job is handed back to that same
        # client through the canonical external-cataloging protocol.
        model = None
        provider = str(provider_override or "external_agent").strip().lower()
        selection_source = "external_agent"
    else:
        selection = cataloging_model_selection(model_override)
        model = selection.model
        provider = str(
            provider_override
            or selection.provider
            or (model or "").split(":", 1)[0]
            or ""
        ).strip().lower()
        selection_source = str(selection.source or "default").strip()
    if not backend:
        backend = "local_cli_agent" if is_local_cli_provider(provider) else "internal_llm"

    cancelled = (
        cancel_superseded_chapter_cataloging_jobs(db, project_id, chapter_ids)
        if trigger_source == CHAPTER_SAVE_SOURCE
        else []
    )
    model_source = f"{trigger_source}:{selection_source}"[:50]
    job = create_cataloging_job(
        db,
        project_id,
        execution_mode,
        model,
        chapter_ids,
        execution_backend=backend,
        model_source=model_source,
        provider=provider or None,
    )
    if trigger_source == CHAPTER_SAVE_SOURCE and job.operation_id:
        operation = (
            db.query(OperationRun)
            .filter(OperationRun.id == job.operation_id)
            .first()
        )
        chapters = (
            db.query(Chapter)
            .filter(
                Chapter.project_id == project_id,
                Chapter.id.in_(chapter_ids),
            )
            .all()
        )
        chapter_titles = [str(chapter.title or "未命名章节").strip() for chapter in chapters]
        chapter_label = (
            f"《{chapter_titles[0]}》"
            if len(chapter_titles) == 1
            else f"{len(chapter_titles)} 个章节"
        )
        if operation:
            operation.title = f"{chapter_label}章节建档"[:300]
            operation.tool_mode = f"chapter_save:{backend}"[:80]
            operation.current_message = (
                f"{chapter_label}已保存，作者已启动建档。"
                "下一章写作已锁定；只有当前版本建档完成后才会解锁。"
            )
            commit_session(db)
    queued = False
    if run_now and backend != "external_agent":
        queue_cataloging_job(job.id)
        queued = True
        logger.info(
            "Cataloging job queued job_id=%s project=%s chapters=%d source=%s backend=%s",
            job.id,
            project_id,
            len(chapter_ids),
            trigger_source,
            backend,
        )
    data = job_to_dict(job)
    data.update({
        "started": run_now,
        "worker_queued": queued,
        "trigger_source": trigger_source,
        "superseded_job_ids": cancelled,
        "next_action": (
            "background_cataloging"
            if queued
            else "continue_external_cataloging"
        ),
    })
    return job, data


async def run_cataloging_job(job_id: str) -> None:
    """Start the worker appropriate for a previously committed job."""

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOB_STATUSES:
            return
        logger.info(
            "Cataloging worker start job=%s project=%s backend=%s",
            job.id,
            job.project_id,
            job.execution_backend,
        )
        if job.execution_backend == "local_cli_agent":
            ensure_local_cli_cataloging_worker(db, job, provider=job.provider)
            return
        if job.execution_backend == "external_agent":
            return
        project_id = job.project_id
    except Exception as exc:
        db.rollback()
        mark_cataloging_worker_failure(
            job_id,
            f"章节建档启动失败：{exc}",
            failure_class=type(exc).__name__,
        )
        return
    finally:
        db.close()

    try:
        async for _event in stream_cataloging_job(project_id, job_id):
            pass
    except asyncio.CancelledError as exc:
        mark_cataloging_worker_failure(
            job_id,
            "章节建档 worker 被中断",
            failure_class="interrupted",
        )
        raise exc
    except Exception as exc:
        mark_cataloging_worker_failure(
            job_id,
            f"章节建档执行失败：{exc}",
            failure_class=type(exc).__name__,
        )
        logger.exception("Cataloging worker failed for %s", job_id)


def queue_cataloging_job(job_id: str) -> asyncio.Task[None]:
    existing = _LAUNCH_TASKS.get(job_id)
    if existing and not existing.done():
        return existing
    task = asyncio.create_task(
        run_cataloging_job(job_id),
        name=f"cataloging-launch-{job_id}",
    )
    _LAUNCH_TASKS[job_id] = task
    task.add_done_callback(lambda _task: _LAUNCH_TASKS.pop(job_id, None))
    return task


__all__ = [
    "CHAPTER_SAVE_SOURCE",
    "cancel_superseded_chapter_cataloging_jobs",
    "cataloging_block_result",
    "cataloging_required_block_result",
    "create_and_queue_cataloging_job",
    "find_blocking_chapter_cataloging_job",
    "find_cataloging_required_chapter",
    "mark_cataloging_worker_failure",
    "mark_interrupted_cataloging_jobs",
    "queue_cataloging_job",
    "run_cataloging_job",
]
