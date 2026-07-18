"""Project cataloging endpoints."""
from __future__ import annotations

from app.architecture.uow import commit_session

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import SessionLocal, get_db
from ..modules.continuity.interfaces.cataloging_dependencies import cataloging_queries
from ..schemas.cataloging import (
    CatalogingCandidateBulkUpdate,
    CatalogingCandidateCreate,
    CatalogingCandidateUpdate,
    CatalogingModeUpdate,
    CatalogingStartRequest,
)
from ..services.cataloging.applier import apply_candidates_for_run
from ..services.cataloging.candidate_io import candidate_payload, candidate_to_dict
from ..services.cataloging.job_control import (
    cancel_job,
    first_blocking_run,
    mark_run_skipped,
    pause_job,
    refresh_job_progress,
    reset_run_for_retry,
    reset_run_for_resolution_retry,
    resume_job,
)
from ..services.cataloging.fact_store import fact_to_dict, load_facts_for_run
from ..services.cataloging.lookups import find_character_by_name_or_id
from ..services.cataloging.manual_ops import create_manual_candidate, has_usable_chapter_summary, recover_failed_run_for_review
from ..services.cataloging.model_selection import cataloging_model_selection
from ..services.cataloging.orchestrator import create_cataloging_job, job_to_dict, run_to_dict, stream_cataloging_job
from ..services.cataloging.local_cli_agent import (
    cancel_local_cli_cataloging_worker,
    ensure_local_cli_cataloging_worker,
    stream_local_cli_cataloging_job,
)
from ..ai.local_cli_adapter import is_local_cli_provider
from ..services.character_merge_service import build_character_merge_preview

router = APIRouter(tags=["cataloging"])


def _get_job_or_404(db: Session, project_id: str, job_id: str):
    job = cataloging_queries(db).get_job(project_id, job_id)
    if not job:
        raise NotFoundError("作品建档任务不存在")
    return job


def _get_candidate_or_404(db: Session, project_id: str, candidate_id: str):
    candidate = cataloging_queries(db).get_candidate(project_id, candidate_id)
    if not candidate:
        raise NotFoundError("候选项不存在")
    return candidate


@router.post("/projects/{project_id}/cataloging/start")
async def start_cataloging(project_id: str, payload: CatalogingStartRequest, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    selection = cataloging_model_selection(payload.model)
    model = selection.model
    provider = (selection.provider or (model or "").split(":", 1)[0]).lower()
    if provider == "local_llama_cpp" and selection.source != "explicit":
        raise ValidationError(
            "本地 llama.cpp 模型上下文不足，不能自动用于作品建档；"
            "请选择 API/本机 CLI 模型，或在高级设置中显式选择本地模型进行短章节实验。"
        )
    local_cli = is_local_cli_provider(provider)
    job = create_cataloging_job(
        db,
        project_id,
        payload.execution_mode,
        model,
        payload.chapter_ids,
        execution_backend="local_cli_agent" if local_cli else "internal_llm",
        model_source=selection.source,
        provider=provider or None,
    )
    if local_cli:
        try:
            ensure_local_cli_cataloging_worker(db, job, provider=provider)
        except Exception as exc:
            job.status = "paused_on_failure"
            job.error = str(exc)
            commit_session(db)
            raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=job_to_dict(job), message="作品建档任务已创建")


@router.get("/projects/{project_id}/cataloging/jobs")
def list_cataloging_jobs(project_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    jobs = cataloging_queries(db).list_jobs(project_id, limit=20)
    return ApiResponse.success(data={"items": [job_to_dict(job) for job in jobs], "total": len(jobs)})


@router.get("/projects/{project_id}/cataloging/{job_id}")
def get_cataloging_job(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    runs = cataloging_queries(db).list_runs(job.id)
    return ApiResponse.success(data={"job": job_to_dict(job), "runs": [run_to_dict(run) for run in runs]})


@router.post("/projects/{project_id}/cataloging/{job_id}/stream")
async def stream_cataloging(project_id: str, job_id: str):
    db = SessionLocal()
    try:
        job = _get_job_or_404(db, project_id, job_id)
        local_cli = job.execution_backend == "local_cli_agent"
    finally:
        db.close()
    return StreamingResponse(
        (
            stream_local_cli_cataloging_job(project_id, job_id)
            if local_cli
            else stream_cataloging_job(project_id, job_id)
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/projects/{project_id}/cataloging/{job_id}/mode")
def update_cataloging_mode(project_id: str, job_id: str, payload: CatalogingModeUpdate, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    job.execution_mode = payload.execution_mode
    job.updated_at = datetime.utcnow()
    should_resume = job.status == "waiting_confirmation" and payload.execution_mode == "auto"
    if should_resume and job.execution_backend == "local_cli_agent":
        job.status = "running"
        job.blocked_chapter_id = None
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "should_resume": should_resume})


@router.get("/projects/{project_id}/cataloging/{job_id}/candidates")
def list_cataloging_candidates(
    project_id: str,
    job_id: str,
    chapter_run_id: str | None = Query(None),
    status: str | None = Query(None),
    item_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    candidates = cataloging_queries(db).list_candidates(
        job.id,
        chapter_run_id=chapter_run_id,
        status=status,
        item_type=item_type,
    )
    return ApiResponse.success(data={"items": [candidate_to_dict(item) for item in candidates], "total": len(candidates)})


@router.get("/projects/{project_id}/cataloging/{job_id}/facts")
def list_cataloging_facts(
    project_id: str,
    job_id: str,
    chapter_run_id: str | None = Query(None),
    fact_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    facts = cataloging_queries(db).list_facts(
        job.id,
        chapter_run_id=chapter_run_id,
        fact_type=fact_type,
    )
    return ApiResponse.success(data={"items": [fact_to_dict(item) for item in facts], "total": len(facts)})


@router.get("/projects/{project_id}/cataloging/candidates/{candidate_id}/merge-preview")
def get_character_merge_candidate_preview(project_id: str, candidate_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    candidate = _get_candidate_or_404(db, project_id, candidate_id)
    if candidate.item_type != "character_merge_candidate":
        raise ValidationError("只有角色合并候选项可以查看合并预览")

    payload = candidate_payload(candidate)
    primary_name = payload.get("primary_name") or payload.get("canonical_name")
    secondary_name = payload.get("secondary_name")
    primary = find_character_by_name_or_id(db, project_id, primary_name)
    secondary = find_character_by_name_or_id(db, project_id, secondary_name)
    preview = None
    if primary and secondary and primary.id != secondary.id:
        preview = build_character_merge_preview(db, project_id, primary.id, secondary.id, payload)
    return ApiResponse.success(data={
        "candidate": candidate_to_dict(candidate),
        "payload": payload,
        "primary": preview["primary"] if preview else None,
        "secondary": preview["secondary"] if preview else None,
        "preview": preview,
    })


@router.patch("/projects/{project_id}/cataloging/candidates/{candidate_id}")
def update_cataloging_candidate(
    project_id: str,
    candidate_id: str,
    payload: CatalogingCandidateUpdate,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    candidate = _get_candidate_or_404(db, project_id, candidate_id)
    if candidate.status in {"applying", "applied"}:
        raise ValidationError("候选项正在写入或已写入，不能修改")
    if payload.payload is not None:
        candidate.edited_payload = json.dumps(payload.payload, ensure_ascii=False)
        if payload.status is None and candidate.status == "pending":
            candidate.status = "edited"
    if payload.status is not None:
        candidate.status = payload.status
    candidate.updated_at = datetime.utcnow()
    commit_session(db)
    db.refresh(candidate)
    return ApiResponse.success(data=candidate_to_dict(candidate), message="候选项已更新")


@router.post("/projects/{project_id}/cataloging/{job_id}/candidates")
def create_cataloging_candidate(
    project_id: str,
    job_id: str,
    payload: CatalogingCandidateCreate,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    if payload.chapter_run_id:
        run = cataloging_queries(db).get_run(job.id, payload.chapter_run_id)
    else:
        run = first_blocking_run(db, job)
    if not run:
        raise ValidationError("当前没有可补充候选项的章节")
    try:
        candidate = create_manual_candidate(
            db,
            job,
            run,
            payload.item_type,
            payload.payload,
            payload.status,
            target_name=payload.target_name,
            confidence=payload.confidence,
            evidence=payload.evidence,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    commit_session(db)
    db.refresh(candidate)
    return ApiResponse.success(data=candidate_to_dict(candidate), message="候选项已新增")


@router.patch("/projects/{project_id}/cataloging/{job_id}/candidates/bulk")
def bulk_update_cataloging_candidates(
    project_id: str,
    job_id: str,
    payload: CatalogingCandidateBulkUpdate,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    candidates = cataloging_queries(db).list_candidates(
        job.id,
        candidate_ids=payload.candidate_ids,
    )
    for candidate in candidates:
        if candidate.status in {"applying", "applied"}:
            continue
        candidate.status = payload.status
        candidate.updated_at = datetime.utcnow()
    commit_session(db)
    return ApiResponse.success(
        data={"items": [candidate_to_dict(item) for item in candidates], "total": len(candidates)},
        message="候选项已批量更新",
    )


@router.post("/projects/{project_id}/cataloging/{job_id}/apply-pending")
def apply_pending_cataloging(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    run = cataloging_queries(db).first_awaiting_confirmation(job.id)
    if not run:
        raise ValidationError("当前没有等待确认的章节")
    events = apply_candidates_for_run(db, job, run)
    has_failed = any(event["type"] == "candidate_apply_failed" for event in events)
    run.status = "completed_with_warnings" if has_failed else "completed"
    run.completed_at = datetime.utcnow()
    job.status = "running"
    job.last_completed_chapter_id = run.chapter_id
    job.blocked_chapter_id = None
    job.error = None
    refresh_job_progress(db, job)
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "run": run_to_dict(run), "events": events}, message="候选项已写入")


@router.post("/projects/{project_id}/cataloging/{job_id}/skip-current")
def skip_current_cataloging_chapter(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    run = first_blocking_run(db, job)
    if not run:
        raise ValidationError("当前没有可跳过的章节")
    mark_run_skipped(db, job, run)
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "run": run_to_dict(run)}, message="当前章节已显式跳过")

@router.post("/projects/{project_id}/cataloging/{job_id}/retry-current")
def retry_current_cataloging_chapter(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    run = first_blocking_run(db, job)
    if not run:
        raise ValidationError("当前没有可重试的章节")
    reset_run_for_retry(db, job, run)
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "run": run_to_dict(run)}, message="当前章节已重置，准备重试")


@router.post("/projects/{project_id}/cataloging/{job_id}/rerun-resolution-current")
def rerun_current_cataloging_resolution(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    run = first_blocking_run(db, job)
    if not run:
        run = cataloging_queries(db).first_resolution_candidate(job.id)
    if not run:
        raise ValidationError("当前没有可重跑第二阶段的章节")
    if not load_facts_for_run(db, run):
        raise ValidationError("当前章节没有已保存事实，请使用完整重试")
    reset_run_for_resolution_retry(db, job, run)
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "run": run_to_dict(run)}, message="已保留事实并重置第二阶段")


@router.post("/projects/{project_id}/cataloging/{job_id}/recover-current")
def recover_current_cataloging_chapter(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    run = first_blocking_run(db, job)
    if not run or run.status != "failed":
        raise ValidationError("当前没有可转入人工确认的失败章节")
    if not has_usable_chapter_summary(db, run):
        raise ValidationError("当前章节缺少 chapter_summary，请先手动新增章节摘要候选项")
    recover_failed_run_for_review(db, job, run)
    commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job), "run": run_to_dict(run)}, message="当前章节已转入人工确认")


@router.post("/projects/{project_id}/cataloging/{job_id}/pause")
def pause_cataloging_job(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    if job.status not in {"completed", "cancelled", "failed"}:
        pause_job(job)
        commit_session(db)
        if job.execution_backend == "local_cli_agent":
            cancel_local_cli_cataloging_worker(job.id)
    return ApiResponse.success(data={"job": job_to_dict(job)}, message="作品建档任务已暂停")


@router.post("/projects/{project_id}/cataloging/{job_id}/resume")
def resume_cataloging_job(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    if job.status == "paused":
        resume_job(job)
        commit_session(db)
    return ApiResponse.success(data={"job": job_to_dict(job)}, message="作品建档任务已继续")


@router.post("/projects/{project_id}/cataloging/{job_id}/cancel")
def cancel_cataloging_job(project_id: str, job_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    job = _get_job_or_404(db, project_id, job_id)
    if job.status in {"completed", "cancelled"}:
        return ApiResponse.success(data={"job": job_to_dict(job)}, message="任务已结束")
    cancel_job(job)
    commit_session(db)
    if job.execution_backend == "local_cli_agent":
        cancel_local_cli_cataloging_worker(job.id, terminal=True)
    return ApiResponse.success(data={"job": job_to_dict(job)}, message="作品建档任务已取消")
