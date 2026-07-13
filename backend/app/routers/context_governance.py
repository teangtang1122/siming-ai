"""HTTP API for context manifests, profiles and index rebuilds."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import ContextManifest, ContextRebuildJob, ContextRebuildProject, ModelContextProfile
from ..database.session import get_db
from ..schemas.context_governance import (
    ContextEvidenceSubmission,
    ContextManifestOverride,
    ContextManifestPrepare,
    ContextRebuildRequest,
    ContextSearchRequest,
    ModelContextProfilePayload,
)
from ..services.context_orchestrator import (
    ContextOrchestrator,
    run_context_rebuild_job,
)


router = APIRouter(tags=["context-governance"])


def _job_payload(job: ContextRebuildJob) -> dict:
    return {
        "id": job.id,
        "policy_version": job.policy_version,
        "status": job.status,
        "requested_by": job.requested_by,
        "total_projects": job.total_projects,
        "completed_projects": job.completed_projects,
        "failed_projects": job.failed_projects,
        "semantic_available": bool(job.semantic_available),
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "projects": [
            {
                "id": item.id,
                "project_id": item.project_id,
                "status": item.status,
                "index_version": item.index_version,
                "current_source_type": item.current_source_type,
                "indexed_chunks": item.indexed_chunks,
                "semantic_chunks": item.semantic_chunks,
                "error": item.error,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "completed_at": item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in job.projects
        ],
    }


@router.get("/context-governance/model-profiles")
def list_model_profiles(db: Session = Depends(get_db)):
    orchestrator = ContextOrchestrator(db)
    rows = db.query(ModelContextProfile).order_by(ModelContextProfile.provider, ModelContextProfile.model_name).all()
    return ApiResponse.success(data={
        "items": [
            {
                "id": row.id,
                "provider": row.provider,
                "model_name": row.model_name,
                "context_window_tokens": row.context_window_tokens,
                "max_output_tokens": row.max_output_tokens,
                "safety_margin_tokens": row.safety_margin_tokens,
                "enabled": bool(row.enabled),
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ],
        "fallback": {"context_window_tokens": 16384, "reason": "Unknown models use a conservative 16K profile."},
        "semantic": orchestrator.semantic_status(),
    })


@router.put("/context-governance/model-profiles")
def save_model_profile(payload: ModelContextProfilePayload, db: Session = Depends(get_db)):
    row = (
        db.query(ModelContextProfile)
        .filter(
            ModelContextProfile.provider == payload.provider.strip(),
            ModelContextProfile.model_name == payload.model_name.strip(),
        )
        .first()
    )
    if row is None:
        row = ModelContextProfile(provider=payload.provider.strip(), model_name=payload.model_name.strip())
        db.add(row)
    row.context_window_tokens = payload.context_window_tokens
    row.max_output_tokens = payload.max_output_tokens
    row.safety_margin_tokens = payload.safety_margin_tokens
    row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return ApiResponse.success(data={
        "id": row.id,
        "provider": row.provider,
        "model_name": row.model_name,
        "context_window_tokens": row.context_window_tokens,
        "max_output_tokens": row.max_output_tokens,
        "safety_margin_tokens": row.safety_margin_tokens,
        "enabled": bool(row.enabled),
    })


@router.get("/context-governance/rebuilds")
def list_rebuilds(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    jobs = db.query(ContextRebuildJob).order_by(ContextRebuildJob.created_at.desc()).limit(limit).all()
    return ApiResponse.success(data={"items": [_job_payload(job) for job in jobs]})


@router.post("/context-governance/rebuilds")
def create_rebuild(payload: ContextRebuildRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    orchestrator = ContextOrchestrator(db)
    job = orchestrator.create_rebuild_job(requested_by=payload.requested_by, project_ids=payload.project_ids)
    db.commit()
    db.refresh(job)
    if job.status == "queued":
        background_tasks.add_task(run_context_rebuild_job, job.id)
    return ApiResponse.success(data=_job_payload(job), message="Context rebuild queued")


@router.post("/context-governance/rebuilds/{job_id}/retry")
def retry_rebuild(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
    if not job:
        raise NotFoundError("Context rebuild job not found.")
    ContextOrchestrator(db).retry_rebuild_job(job)
    db.commit()
    db.refresh(job)
    if job.status == "queued":
        background_tasks.add_task(run_context_rebuild_job, job.id)
    return ApiResponse.success(data=_job_payload(job), message="Context rebuild retry queued")


@router.get("/projects/{project_id}/context-manifests")
def list_manifests(project_id: str, limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    rows = (
        db.query(ContextManifest)
        .filter(ContextManifest.project_id == project_id)
        .order_by(ContextManifest.created_at.desc())
        .limit(limit)
        .all()
    )
    return ApiResponse.success(data={"items": [orchestrator.manifest_payload(row, include_content=False) for row in rows]})


@router.post("/projects/{project_id}/context-manifests/prepare")
def prepare_manifest(project_id: str, payload: ContextManifestPrepare, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    manifest = ContextOrchestrator(db).prepare(
        project_id=project_id,
        task_type=payload.task_type,
        model=payload.model,
        execution_route=payload.execution_route,
        arguments=payload.arguments,
        session_id=payload.session_id,
        pinned_chunk_ids=payload.pinned_chunk_ids,
        pinned_source_ids=payload.pinned_source_ids,
    )
    db.commit()
    db.refresh(manifest)
    return ApiResponse.success(data=ContextOrchestrator(db).manifest_payload(manifest))


@router.get("/projects/{project_id}/context-manifests/{manifest_id}")
def get_manifest(project_id: str, manifest_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    return ApiResponse.success(data=orchestrator.manifest_payload(manifest))


@router.get("/projects/{project_id}/context-manifests/{manifest_id}/explain")
def explain_manifest(project_id: str, manifest_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    return ApiResponse.success(data=orchestrator.explain(manifest))


@router.post("/projects/{project_id}/context-manifests/{manifest_id}/override")
def override_manifest(project_id: str, manifest_id: str, payload: ContextManifestOverride, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    try:
        orchestrator.override(manifest, reason=payload.reason, actor=payload.actor)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    db.commit()
    return ApiResponse.success(data=orchestrator.manifest_payload(manifest), message="Context override recorded")


@router.post("/projects/{project_id}/context-manifests/{manifest_id}/search")
def search_manifest(project_id: str, manifest_id: str, payload: ContextSearchRequest, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    rows = orchestrator.search_task_context(manifest, query=payload.query, limit=payload.limit)
    db.commit()
    return ApiResponse.success(data={"manifest_id": manifest.id, "items": rows})


@router.post("/projects/{project_id}/context-manifests/{manifest_id}/evidence")
def submit_evidence(project_id: str, manifest_id: str, payload: ContextEvidenceSubmission, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    result = orchestrator.submit_evidence(manifest, payload.sources)
    db.commit()
    return ApiResponse.success(data={"manifest_id": manifest.id, **result})


@router.get("/projects/{project_id}/context-governance-status")
def project_context_status(project_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    reason = orchestrator.project_rebuild_block_reason(project_id)
    return ApiResponse.success(data={
        "generation_allowed": not bool(reason),
        "reason": reason,
        "semantic": orchestrator.semantic_status(),
    })
