"""Create and launch the canonical cataloging pipeline.

Author-triggered chapter cataloging and the cataloging UI enter the canonical
worker through this module.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from functools import wraps
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
from ...database.write_coordination import DatabaseWriteCoordinator, sqlite_database_path
from .constants import JOB_RUNNING_STATUSES
from .context import ordered_chapters
from .job_control import cancel_job, refresh_job_progress
from .local_cli_agent import (
    cancel_local_cli_cataloging_worker,
    ensure_local_cli_cataloging_worker,
)
from .model_selection import cataloging_model_selection
from .orchestrator import create_cataloging_job, job_to_dict, stream_cataloging_job

CHAPTER_SAVE_SOURCE = "chapter_save"
_LAUNCH_TASKS: dict[str, asyncio.Task[None]] = {}
_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
_NON_REUSABLE_JOB_STATUSES = {"failed", "cancelled"}
_NON_REUSABLE_RUN_STATUSES = {"failed", "skipped_by_user"}
logger = logging.getLogger(__name__)


def _serialized_cataloging_launch(function):
    """Serialize the idempotency check across desktop and MCP processes."""

    @wraps(function)
    def wrapped(db: Session, project_id: str, *args: Any, **kwargs: Any):
        bind = db.get_bind()
        database_path = sqlite_database_path(str(bind.url)) if bind is not None else None
        lease = nullcontext()
        if database_path is not None:
            coordinator = DatabaseWriteCoordinator(
                database_path.with_name(f"{database_path.name}.cataloging-launch"),
                timeout=30.0,
            )
            lease = coordinator.acquire()
        with lease:
            return function(db, project_id, *args, **kwargs)

    return wrapped


def cancel_cataloging_runtime(job_ids: list[str]) -> None:
    """Stop committed cataloging jobs without opening another transaction.

    Chapter rollback marks jobs cancelled inside its owning database
    transaction, then calls this function from ``Session.after_commit``.  That
    ordering prevents an external process from surviving a successful rollback
    while also avoiding irreversible process cancellation when the database
    transaction itself fails.
    """

    for job_id in dict.fromkeys(str(item) for item in job_ids if str(item)):
        task = _LAUNCH_TASKS.get(job_id)
        if task is not None and not task.done():
            loop = task.get_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()
        try:
            cancel_local_cli_cataloging_worker(job_id, terminal=True)
        except Exception:
            logger.exception("Failed to cancel cataloging runtime %s", job_id)


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
        cancelled.append(job.id)
    if cancelled:
        commit_session(db)
        cancel_cataloging_runtime(cancelled)
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
    return query.order_by(
        CatalogingJob.updated_at.desc(),
        CatalogingJob.created_at.desc(),
    ).first()


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
    operation = (
        db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
    )
    if operation:
        operation.failure_class = failure_class[:80]
        operation.health_status = (
            "disconnected" if failure_class == "interrupted" else "stalled"
        )
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


def _reusable_current_version_runs(
    db: Session,
    project_id: str,
    chapters: list[Chapter],
) -> dict[str, CatalogingChapterRun]:
    """Return one authoritative existing run for each unchanged chapter.

    A completed current-version projection is already the desired result.  A
    nonterminal current-version run is already doing the work.  Reusing either
    makes repeated clicks and API/CLI retries idempotent without suppressing a
    new job after the saved chapter version changes.
    """

    if not chapters:
        return {}
    versions = {
        chapter.id: int(chapter.current_version or 1)
        for chapter in chapters
    }
    rows = (
        db.query(CatalogingChapterRun)
        .join(CatalogingJob, CatalogingJob.id == CatalogingChapterRun.job_id)
        .filter(
            CatalogingChapterRun.project_id == project_id,
            CatalogingChapterRun.chapter_id.in_(list(versions)),
            CatalogingChapterRun.status.notin_(_NON_REUSABLE_RUN_STATUSES),
            CatalogingJob.status.notin_(_NON_REUSABLE_JOB_STATUSES),
        )
        .order_by(
            CatalogingChapterRun.updated_at.desc(),
            CatalogingChapterRun.created_at.desc(),
        )
        .all()
    )
    selected: dict[str, CatalogingChapterRun] = {}
    for run in rows:
        if run.chapter_id in selected:
            continue
        if run.chapter_version is None:
            continue
        if int(run.chapter_version) != versions.get(run.chapter_id):
            continue
        selected[run.chapter_id] = run
    return selected


def _reused_cataloging_launch(
    runs: dict[str, CatalogingChapterRun],
    chapters: list[Chapter],
    *,
    deferred_chapter_ids: list[str] | None = None,
) -> tuple[CatalogingJob, dict[str, Any]]:
    run = max(
        runs.values(),
        key=lambda item: (item.updated_at or item.created_at, item.id),
    )
    job = run.job
    completed_statuses = {"completed", "completed_with_warnings"}
    completed_ids = sorted(
        chapter_id
        for chapter_id, item in runs.items()
        if item.status in completed_statuses
    )
    in_progress_ids = sorted(set(runs) - set(completed_ids))
    deferred_ids = list(deferred_chapter_ids or [])
    data = job_to_dict(job)
    data.update({
        "started": bool(in_progress_ids),
        "worker_queued": False,
        "existing_worker": bool(in_progress_ids),
        "trigger_source": "idempotent_reuse",
        "superseded_job_ids": [],
        "idempotent_reuse": True,
        "requested_chapter_ids": [chapter.id for chapter in chapters],
        "already_cataloged_chapter_ids": completed_ids,
        "in_progress_chapter_ids": in_progress_ids,
        "queued_chapter_ids": [],
        "deferred_chapter_ids": deferred_ids,
        "reused_job_ids": sorted({item.job_id for item in runs.values()}),
        "next_action": (
            "await_existing_cataloging"
            if in_progress_ids or deferred_ids
            else "already_cataloged"
        ),
    })
    return job, data


@_serialized_cataloging_launch
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

    chapters = ordered_chapters(db, project_id, chapter_ids)
    reusable = _reusable_current_version_runs(db, project_id, chapters)
    missing_chapters = [chapter for chapter in chapters if chapter.id not in reusable]
    active_reused = {
        chapter_id: run
        for chapter_id, run in reusable.items()
        if run.status not in {"completed", "completed_with_warnings"}
    }
    if chapters and (not missing_chapters or active_reused):
        return _reused_cataloging_launch(
            reusable,
            chapters,
            deferred_chapter_ids=[chapter.id for chapter in missing_chapters],
        )

    backend = str(backend_override or "").strip()
    if backend == "external_agent" and not model_override:
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
        backend = (
            "local_cli_agent" if is_local_cli_provider(provider) else "internal_llm"
        )

    queued_chapter_ids = [chapter.id for chapter in missing_chapters]
    cancelled = (
        cancel_superseded_chapter_cataloging_jobs(db, project_id, queued_chapter_ids)
        if trigger_source == CHAPTER_SAVE_SOURCE
        else []
    )
    model_source = f"{trigger_source}:{selection_source}"[:50]
    job = create_cataloging_job(
        db,
        project_id,
        execution_mode,
        model,
        queued_chapter_ids,
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
        chapter_titles = [
            str(chapter.title or "未命名章节").strip()
            for chapter in chapters
        ]
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
    queued = queue_managed_cataloging_job(job, run_now=run_now)
    data = job_to_dict(job)
    data.update(
        {
            "started": run_now,
            "worker_queued": queued,
            "trigger_source": trigger_source,
            "superseded_job_ids": cancelled,
            "idempotent_reuse": False,
            "requested_chapter_ids": [chapter.id for chapter in chapters],
            "already_cataloged_chapter_ids": sorted(reusable),
            "in_progress_chapter_ids": [],
            "queued_chapter_ids": queued_chapter_ids,
            "deferred_chapter_ids": [],
            "reused_job_ids": sorted({run.job_id for run in reusable.values()}),
            "next_action": (
                "background_cataloging"
                if queued
                else "continue_external_cataloging"
            ),
        }
    )
    return job, data


async def run_cataloging_job(job_id: str) -> None:
    """Start the worker appropriate for a previously committed job."""

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOB_STATUSES:
            return
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


def queue_managed_cataloging_job(
    job: CatalogingJob,
    *,
    run_now: bool = True,
) -> bool:
    """Queue the canonical worker only for Siming-managed backends.

    REST, workspace/MCP, and author-save entry points all use this predicate so
    resetting or resuming the same job cannot silently behave differently by
    transport.  External-agent jobs remain caller-driven.
    """

    if not run_now or job.execution_backend == "external_agent":
        return False
    queue_cataloging_job(job.id)
    return True


__all__ = [
    "CHAPTER_SAVE_SOURCE",
    "cancel_cataloging_runtime",
    "cancel_superseded_chapter_cataloging_jobs",
    "cataloging_block_result",
    "cataloging_required_block_result",
    "create_and_queue_cataloging_job",
    "find_blocking_chapter_cataloging_job",
    "find_cataloging_required_chapter",
    "mark_cataloging_worker_failure",
    "mark_interrupted_cataloging_jobs",
    "queue_managed_cataloging_job",
    "queue_cataloging_job",
    "run_cataloging_job",
]
