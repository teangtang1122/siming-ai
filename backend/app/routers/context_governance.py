"""HTTP API for context manifests, profiles and index rebuilds."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.context.application.governance import get_context_governance
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


@router.get("/context-governance/model-profiles")
def list_model_profiles(db: Session = Depends(get_db)):
    return ApiResponse.success(data=get_context_governance().list_profiles(db))


@router.put("/context-governance/model-profiles")
def save_model_profile(payload: ModelContextProfilePayload, db: Session = Depends(get_db)):
    return ApiResponse.success(
        data=get_context_governance().save_profile(db, payload.model_dump())
    )


@router.get("/context-governance/rebuilds")
def list_rebuilds(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return ApiResponse.success(data={"items": get_context_governance().list_rebuilds(db, limit)})


@router.post("/context-governance/rebuilds")
def create_rebuild(payload: ContextRebuildRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = get_context_governance().create_rebuild(db, payload.model_dump())
    if job["status"] == "queued":
        background_tasks.add_task(run_context_rebuild_job, job["id"])
    return ApiResponse.success(data=job, message="Context rebuild queued")


@router.post("/context-governance/rebuilds/{job_id}/retry")
def retry_rebuild(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = get_context_governance().retry_rebuild(db, job_id)
    if not job:
        raise NotFoundError("Context rebuild job not found.")
    if job["status"] == "queued":
        background_tasks.add_task(run_context_rebuild_job, job["id"])
    return ApiResponse.success(data=job, message="Context rebuild retry queued")


@router.get("/projects/{project_id}/context-manifests")
def list_manifests(project_id: str, limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return ApiResponse.success(
        data={"items": get_context_governance().list_manifests(db, project_id, limit)}
    )


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
    commit_session(db)
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
    commit_session(db)
    return ApiResponse.success(data=orchestrator.manifest_payload(manifest), message="Context override recorded")


@router.post("/projects/{project_id}/context-manifests/{manifest_id}/search")
def search_manifest(project_id: str, manifest_id: str, payload: ContextSearchRequest, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    rows = orchestrator.search_task_context(manifest, query=payload.query, limit=payload.limit)
    commit_session(db)
    return ApiResponse.success(data={"manifest_id": manifest.id, "items": rows})


@router.post("/projects/{project_id}/context-manifests/{manifest_id}/evidence")
def submit_evidence(project_id: str, manifest_id: str, payload: ContextEvidenceSubmission, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if not manifest:
        raise NotFoundError("Context manifest not found.")
    result = orchestrator.submit_evidence(manifest, payload.sources)
    commit_session(db)
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
