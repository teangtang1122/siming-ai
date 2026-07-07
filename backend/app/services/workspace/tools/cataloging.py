"""Cataloging workspace tools for project bootstrapping jobs."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingFact, CatalogingJob
from ....services.content_store import sync_project_to_files
from ....services.cataloging.applier import apply_candidates_for_run
from ....services.cataloging.candidate_validation import inspect_candidate_coverage
from ....services.cataloging.candidate_io import candidate_to_dict
from ....services.cataloging.fact_store import fact_to_dict, load_facts_for_run
from ....services.cataloging.job_control import (
    cancel_job,
    first_blocking_run,
    pause_job,
    refresh_job_progress,
    reset_run_for_resolution_retry,
    reset_run_for_retry,
    resume_job,
)
from ....services.cataloging.model_selection import cataloging_model_selection
from ....services.cataloging.orchestrator import create_cataloging_job, job_to_dict, run_to_dict, stream_cataloging_job
from ....services.cataloging.local_cli_agent import (
    cancel_local_cli_cataloging_worker,
    ensure_local_cli_cataloging_worker,
)
from ....ai.local_cli_adapter import is_local_cli_provider


async def _consume_cataloging_stream(project_id: str, job_id: str) -> None:
    async for _event in stream_cataloging_job(project_id, job_id):
        pass


def _get_job(db: Session, project_id: str, args: dict[str, Any]) -> CatalogingJob | None:
    job_id = str(args.get("job_id") or args.get("id") or "").strip()
    if not job_id:
        return None
    job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
    if not job:
        return None
    if project_id and job.project_id != project_id:
        return None
    return job


def _managed_cataloging_bindings() -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for prefix in ("SIMING", "MOSHU"):
        managed_kind = os.environ.get(f"{prefix}_MANAGED_AGENT_KIND", "")
        if managed_kind.strip().lower() != "cataloging":
            continue
        bindings.append({
            "project_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_PROJECT_ID", "").strip(),
            "job_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_JOB_ID", "").strip(),
            "chapter_run_id": os.environ.get(f"{prefix}_MANAGED_CATALOGING_CHAPTER_RUN_ID", "").strip(),
        })
    return bindings


def _managed_cataloging_run_id(job: CatalogingJob) -> str:
    for binding in _managed_cataloging_bindings():
        bound_project = binding["project_id"]
        bound_job = binding["job_id"]
        if bound_project and bound_project != job.project_id:
            continue
        if bound_job and bound_job != job.id:
            continue
        return binding["chapter_run_id"]
    return ""


async def start_cataloging_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    mode = str(args.get("execution_mode") or "auto")
    if mode not in {"auto", "manual"}:
        mode = "auto"
    chapter_ids = args.get("chapter_ids") if isinstance(args.get("chapter_ids"), list) else []
    selection = cataloging_model_selection(args.get("model"))
    model = selection.model
    provider = (selection.provider or (model or "").split(":", 1)[0]).lower()
    local_cli = is_local_cli_provider(provider)
    job = create_cataloging_job(
        db,
        project_id,
        mode,
        model,
        [str(item) for item in chapter_ids],
        execution_backend="local_cli_agent" if local_cli else "internal_llm",
        model_source=selection.source,
        provider=provider or None,
    )
    if bool(args.get("run_now", True)) and local_cli:
        ensure_local_cli_cataloging_worker(db, job, provider=provider)
    elif bool(args.get("run_now", True)):
        asyncio.create_task(_consume_cataloging_stream(project_id, job.id))
    return {
        "tool": "start_cataloging_job",
        "status": "ok",
        "detail": f"已创建作品建档任务，共 {job.total_chapters or 0} 章",
        "data": job_to_dict(job),
    }


async def list_cataloging_jobs(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    limit = max(1, min(50, int(args.get("limit") or 20)))
    jobs = (
        db.query(CatalogingJob)
        .filter(CatalogingJob.project_id == project_id)
        .order_by(CatalogingJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"tool": "list_cataloging_jobs", "status": "ok", "detail": f"共 {len(jobs)} 个建档任务", "data": {"items": [job_to_dict(job) for job in jobs], "total": len(jobs)}}


async def get_cataloging_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "get_cataloging_job", "status": "skipped", "detail": "未找到建档任务"}
    runs = (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .all()
    )
    return {"tool": "get_cataloging_job", "status": "ok", "detail": "已读取建档任务", "data": {"job": job_to_dict(job), "runs": [run_to_dict(run) for run in runs]}}


async def get_cataloging_control_state(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Return the small, live control surface needed by local CLI workers."""
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "get_cataloging_control_state", "status": "skipped", "detail": "未找到建档任务"}
    run = first_blocking_run(db, job) or (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id)
        .filter(CatalogingChapterRun.status.notin_(["completed", "completed_with_warnings", "skipped_by_user"]))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )
    return {
        "tool": "get_cataloging_control_state",
        "status": "ok",
        "detail": "已读取建档控制状态",
        "data": {
            "job_id": job.id,
            "project_id": job.project_id,
            "status": job.status,
            "execution_mode": job.execution_mode,
            "execution_backend": job.execution_backend or "internal_llm",
            "current_chapter_id": job.current_chapter_id,
            "blocked_chapter_id": job.blocked_chapter_id,
            "current_run": run_to_dict(run) if run else None,
        },
    }


async def set_cataloging_mode(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "set_cataloging_mode", "status": "skipped", "detail": "未找到建档任务"}
    mode = str(args.get("execution_mode") or args.get("mode") or "")
    if mode not in {"auto", "manual"}:
        return {"tool": "set_cataloging_mode", "status": "skipped", "detail": "模式必须是 auto 或 manual"}
    job.execution_mode = mode
    db.flush()
    if job.status == "waiting_confirmation" and mode == "auto":
        job.status = "running"
        job.blocked_chapter_id = None
        if job.execution_backend != "local_cli_agent":
            asyncio.create_task(_consume_cataloging_stream(job.project_id, job.id))
    return {"tool": "set_cataloging_mode", "status": "ok", "detail": f"建档模式已切换为 {mode}", "data": job_to_dict(job)}


async def list_cataloging_candidates(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "list_cataloging_candidates", "status": "skipped", "detail": "未找到建档任务"}
    query = db.query(CatalogingCandidate).filter(CatalogingCandidate.job_id == job.id)
    chapter_run_id = _managed_cataloging_run_id(job) or str(args.get("chapter_run_id") or "").strip()
    if chapter_run_id:
        query = query.filter(CatalogingCandidate.chapter_run_id == chapter_run_id)
    if args.get("status"):
        query = query.filter(CatalogingCandidate.status == str(args.get("status")))
    if args.get("item_type"):
        query = query.filter(CatalogingCandidate.item_type == str(args.get("item_type")))
    items = query.order_by(CatalogingCandidate.created_at.asc()).all()
    return {"tool": "list_cataloging_candidates", "status": "ok", "detail": f"共 {len(items)} 个候选写入项", "data": {"items": [candidate_to_dict(item) for item in items], "total": len(items)}}


async def list_cataloging_facts(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "list_cataloging_facts", "status": "skipped", "detail": "未找到建档任务"}
    query = db.query(CatalogingFact).filter(CatalogingFact.job_id == job.id)
    chapter_run_id = _managed_cataloging_run_id(job) or str(args.get("chapter_run_id") or "").strip()
    if chapter_run_id:
        query = query.filter(CatalogingFact.chapter_run_id == chapter_run_id)
    if args.get("fact_type"):
        query = query.filter(CatalogingFact.fact_type == str(args.get("fact_type")))
    items = query.order_by(CatalogingFact.sort_order.asc(), CatalogingFact.created_at.asc()).all()
    return {"tool": "list_cataloging_facts", "status": "ok", "detail": f"共 {len(items)} 条事实", "data": {"items": [fact_to_dict(item) for item in items], "total": len(items)}}


async def update_cataloging_candidate(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    candidate_id = str(args.get("candidate_id") or args.get("id") or "").strip()
    candidate = db.query(CatalogingCandidate).filter(CatalogingCandidate.id == candidate_id, CatalogingCandidate.project_id == project_id).first()
    if not candidate:
        return {"tool": "update_cataloging_candidate", "status": "skipped", "detail": "未找到候选项"}
    if isinstance(args.get("payload"), dict):
        import json

        candidate.edited_payload = json.dumps(args.get("payload"), ensure_ascii=False)
        if candidate.status == "pending":
            candidate.status = "edited"
    if args.get("status"):
        candidate.status = str(args.get("status"))
    db.flush()
    return {"tool": "update_cataloging_candidate", "status": "ok", "detail": "候选项已更新", "data": candidate_to_dict(candidate)}


async def apply_pending_cataloging(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "apply_pending_cataloging", "status": "skipped", "detail": "未找到建档任务"}
    run = (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job.id, CatalogingChapterRun.status == "awaiting_confirmation")
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )
    if not run:
        return {"tool": "apply_pending_cataloging", "status": "skipped", "detail": "当前没有等待确认的章节"}
    candidates = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .all()
    )
    coverage = inspect_candidate_coverage(candidates)
    if not coverage.is_complete:
        run.status = "facts_saved"
        job.status = "running"
        job.blocked_chapter_id = run.chapter_id
        db.flush()
        return {
            "tool": "apply_pending_cataloging",
            "status": "skipped",
            "detail": "候选不完整，未执行写入",
            "data": {
                "job_id": job.id,
                "chapter_id": run.chapter_id,
                "candidate_count": coverage.total,
                "missing_required_items": coverage.missing,
                "next_tool": "save_external_cataloging_candidates",
            },
        }
    events = apply_candidates_for_run(db, job, run)
    has_failed = any(event.get("type") == "candidate_apply_failed" for event in events)
    run.status = "completed_with_warnings" if has_failed else "completed"
    job.status = "running"
    job.blocked_chapter_id = None
    refresh_job_progress(db, job)
    sync_project_to_files(db, job.project_id)
    db.flush()
    if job.execution_mode == "auto" and job.execution_backend != "local_cli_agent":
        asyncio.create_task(_consume_cataloging_stream(job.project_id, job.id))
    data: dict[str, Any] = {"job": job_to_dict(job), "run": run_to_dict(run), "events": events}
    if job.execution_mode == "external_agent":
        data["next_tool"] = "verify_external_cataloging_progress"
        data["workflow_reminder"] = {
            "mode": "external_cataloging_no_api",
            "language_rule": (
                "Use the novel/source language for archive data. For Chinese novels, save Chinese names, "
                "titles, summaries, facts, candidates, aliases, outline nodes, and worldbuilding."
            ),
            "next_tool": "verify_external_cataloging_progress",
            "note": "Verify this chapter was written into project data before moving to the next chapter.",
        }
    return {"tool": "apply_pending_cataloging", "status": "ok", "detail": "候选项已写入", "data": data}


async def retry_current_cataloging_chapter(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "retry_current_cataloging_chapter", "status": "skipped", "detail": "未找到建档任务"}
    run = first_blocking_run(db, job)
    if not run:
        run = (
            db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job.id)
            .filter(CatalogingChapterRun.status == "facts_saved")
            .order_by(CatalogingChapterRun.chapter_order.asc())
            .first()
        )
    if not run:
        return {"tool": "retry_current_cataloging_chapter", "status": "skipped", "detail": "当前没有可重试章节"}
    reset_run_for_retry(db, job, run)
    db.flush()
    if bool(args.get("run_now", True)) and job.execution_backend != "local_cli_agent":
        asyncio.create_task(_consume_cataloging_stream(job.project_id, job.id))
    return {"tool": "retry_current_cataloging_chapter", "status": "ok", "detail": "当前章节已重置并开始重试", "data": {"job": job_to_dict(job), "run": run_to_dict(run)}}


async def rerun_cataloging_resolution_current(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "rerun_cataloging_resolution_current", "status": "skipped", "detail": "未找到建档任务"}
    run = first_blocking_run(db, job)
    if not run:
        run = (
            db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job.id)
            .filter(CatalogingChapterRun.status == "facts_saved")
            .order_by(CatalogingChapterRun.chapter_order.asc())
            .first()
        )
    if not run or not load_facts_for_run(db, run):
        return {"tool": "rerun_cataloging_resolution_current", "status": "skipped", "detail": "当前章节没有可复用事实，无法只重跑第二阶段"}
    reset_run_for_resolution_retry(db, job, run)
    db.flush()
    if bool(args.get("run_now", True)) and job.execution_backend != "local_cli_agent":
        asyncio.create_task(_consume_cataloging_stream(job.project_id, job.id))
    return {"tool": "rerun_cataloging_resolution_current", "status": "ok", "detail": "已保留事实并重跑第二阶段", "data": {"job": job_to_dict(job), "run": run_to_dict(run)}}


async def pause_cataloging_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "pause_cataloging_job", "status": "skipped", "detail": "未找到建档任务"}
    pause_job(job)
    db.flush()
    if job.execution_backend == "local_cli_agent":
        cancel_local_cli_cataloging_worker(job.id)
    return {"tool": "pause_cataloging_job", "status": "ok", "detail": "建档任务已暂停", "data": job_to_dict(job)}


async def resume_cataloging_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "resume_cataloging_job", "status": "skipped", "detail": "未找到建档任务"}
    resume_job(job)
    db.flush()
    if bool(args.get("run_now", True)) and job.execution_backend != "local_cli_agent":
        asyncio.create_task(_consume_cataloging_stream(job.project_id, job.id))
    return {"tool": "resume_cataloging_job", "status": "ok", "detail": "建档任务已继续", "data": job_to_dict(job)}


async def cancel_cataloging_job(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "cancel_cataloging_job", "status": "skipped", "detail": "未找到建档任务"}
    cancel_job(job)
    db.flush()
    if job.execution_backend == "local_cli_agent":
        cancel_local_cli_cataloging_worker(job.id, terminal=True)
    return {"tool": "cancel_cataloging_job", "status": "ok", "detail": "建档任务已取消", "data": job_to_dict(job)}
