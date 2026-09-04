"""Cataloging workspace tools for project bootstrapping jobs."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Query, Session

from ....core.legacy_env import compatible_env_prefixes
from ....architecture.tool_result_policy import DEFAULT_MODEL_RESULT_CONTRACT
from ....database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
)
from ....services.cataloging.candidate_io import candidate_to_dict
from ....services.cataloging.fact_store import SOURCE_FACT_TYPES, fact_to_dict, load_facts_for_run
from ....services.cataloging.job_control import (
    cancel_job,
    first_blocking_run,
    first_retryable_run,
    pause_job,
    reset_run_for_resolution_retry,
    reset_run_for_retry,
    resume_job,
)
from ....services.cataloging.local_cli_agent import (
    cancel_local_cli_cataloging_worker,
)
from ....services.cataloging.launcher import (
    create_and_queue_cataloging_job,
    queue_managed_cataloging_job,
)
from ....services.cataloging.manual_ops import apply_pending_cataloging_run
from ....services.cataloging.orchestrator import (
    job_to_dict,
    run_to_dict,
)


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
    for prefix in compatible_env_prefixes():
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
    job, data = create_and_queue_cataloging_job(
        db,
        project_id,
        [str(item) for item in chapter_ids],
        execution_mode=mode,
        model_override=str(args.get("model") or "").strip() or None,
        trigger_source="manual",
        run_now=bool(args.get("run_now", True)),
    )
    return {
        "tool": "start_cataloging_job",
        "status": "ok",
        "detail": (
            "当前章节版本已有建档结果或正在建档，已复用现有任务"
            if data.get("idempotent_reuse")
            else f"已创建作品建档任务，共 {job.total_chapters or 0} 章"
        ),
        "data": data,
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
        queue_managed_cataloging_job(job)
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
    return _cataloging_page(
        "list_cataloging_candidates", query,
        (CatalogingCandidate.created_at.asc(), CatalogingCandidate.id.asc()),
        candidate_to_dict, job.id, chapter_run_id, args, ("status", "item_type"),
    )


async def list_cataloging_facts(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    job = _get_job(db, project_id, args)
    if not job:
        return {"tool": "list_cataloging_facts", "status": "skipped", "detail": "未找到建档任务"}
    query = db.query(CatalogingFact).filter(
        CatalogingFact.job_id == job.id,
        CatalogingFact.status == "active",
        CatalogingFact.fact_type.in_(SOURCE_FACT_TYPES),
    )
    chapter_run_id = _managed_cataloging_run_id(job) or str(args.get("chapter_run_id") or "").strip()
    if chapter_run_id:
        query = query.filter(CatalogingFact.chapter_run_id == chapter_run_id)
    if args.get("fact_type"):
        query = query.filter(CatalogingFact.fact_type == str(args.get("fact_type")))
    return _cataloging_page(
        "list_cataloging_facts", query,
        (CatalogingFact.sort_order.asc(), CatalogingFact.created_at.asc(), CatalogingFact.id.asc()),
        fact_to_dict, job.id, chapter_run_id, args, ("fact_type",),
    )


def _cataloging_page(
    tool: str, query: Query, ordering: tuple[Any, ...],
    serialize: Callable[[Any], dict[str, Any]], job_id: str, chapter_run_id: str,
    args: dict[str, Any], filter_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Page whole records without clipping their payload, evidence or identifiers."""
    offset = max(0, int(args.get("offset") or 0))
    limit = max(1, min(10, int(args.get("limit") or 2)))
    total = query.count()
    items = [serialize(item) for item in query.order_by(*ordering).offset(offset).limit(limit).all()]
    while True:
        next_offset = offset + len(items) if items and offset + len(items) < total else None
        next_arguments = None
        if next_offset is not None:
            next_arguments = {"job_id": job_id, "offset": next_offset, "limit": limit}
            if chapter_run_id:
                next_arguments["chapter_run_id"] = chapter_run_id
            for field in filter_fields:
                if args.get(field):
                    next_arguments[field] = str(args[field])
        result = {
            "tool": tool,
            "status": "ok",
            "detail": f"返回 {len(items)} 项，共 {total} 项；has_more=true 时按 next_arguments 继续读取。",
            "data": {
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": next_offset is not None,
                "next_offset": next_offset,
                "next_arguments": next_arguments,
            },
        }
        # Keep complete records, advancing only past those actually delivered.
        # A single oversized record still fails closed in the shared projector.
        if len(items) <= 1 or len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= DEFAULT_MODEL_RESULT_CONTRACT.max_json_bytes:
            return result
        items.pop()


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
    try:
        run, events = apply_pending_cataloging_run(db, job)
    except ValueError as exc:
        return {
            "tool": "apply_pending_cataloging",
            "status": "skipped",
            "detail": str(exc),
            "data": {"job_id": job.id},
        }
    if run.status == "failed":
        return {
            "tool": "apply_pending_cataloging",
            "status": "skipped",
            "detail": run.error or "关键候选未完成写入",
            "data": {
                "job": job_to_dict(job),
                "run": run_to_dict(run),
                "events": events,
            },
        }
    worker_queued = False
    if job.status == "running":
        worker_queued = queue_managed_cataloging_job(job)
    data: dict[str, Any] = {"job": job_to_dict(job), "run": run_to_dict(run), "events": events}
    data["worker_queued"] = worker_queued
    if job.execution_backend == "external_agent":
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
    run = first_retryable_run(db, job)
    if not run:
        return {"tool": "retry_current_cataloging_chapter", "status": "skipped", "detail": "当前没有可重试章节"}
    reset_run_for_retry(db, job, run)
    db.flush()
    worker_queued = queue_managed_cataloging_job(
        job,
        run_now=bool(args.get("run_now", True)),
    )
    detail = "当前章节已重置并开始重试" if worker_queued else "当前章节已重置，等待外部 Agent 重试"
    return {
        "tool": "retry_current_cataloging_chapter",
        "status": "ok",
        "detail": detail,
        "data": {
            "job": job_to_dict(job),
            "run": run_to_dict(run),
            "worker_queued": worker_queued,
        },
    }


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
    worker_queued = queue_managed_cataloging_job(
        job,
        run_now=bool(args.get("run_now", True)),
    )
    detail = "已保留事实并开始重跑第二阶段" if worker_queued else "已保留事实，等待外部 Agent 重跑第二阶段"
    return {
        "tool": "rerun_cataloging_resolution_current",
        "status": "ok",
        "detail": detail,
        "data": {
            "job": job_to_dict(job),
            "run": run_to_dict(run),
            "worker_queued": worker_queued,
        },
    }


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
    if bool(args.get("run_now", True)):
        queue_managed_cataloging_job(job)
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
