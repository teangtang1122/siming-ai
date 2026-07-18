"""Narrative governance API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.continuity.application.governance import get_narrative_governance_commands
from ..modules.story.application.chapters import ChapterWorkspace
from ..modules.story.interfaces.chapter_dependencies import get_chapter_workspace
from ..schemas.narrative_governance import (
    CheckpointCreate,
    GovernanceCandidateBatch,
    GovernanceItemPayload,
    GovernanceStatusUpdate,
)
from ..services.narrative_governance import (
    apply_governance_candidates,
    checkpoint_diff,
    governance_context,
    governance_dashboard,
    record_character_state,
    record_quality_metric,
    restore_narrative_checkpoint,
    upsert_causal_edge,
    upsert_foreshadowing,
    upsert_narrative_debt,
)

router = APIRouter(prefix="/projects/{project_id}/narrative-governance", tags=["narrative-governance"])


@router.get("")
def get_dashboard(project_id: str, chapter_id: str = "", view: str = Query("all", pattern="^(all|chapter|due|risk)$"), db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return ApiResponse.success(data=governance_dashboard(db, project_id, chapter_id=chapter_id, view=view))


@router.get("/context")
def get_context(project_id: str, chapter_id: str = "", db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    text = governance_context(db, project_id, chapter_id=chapter_id or None)
    return ApiResponse.success(data={"text": text, "used_chars": len(text)})


@router.post("/items")
def create_item(project_id: str, payload: GovernanceItemPayload, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    kind = payload.type.lower()
    try:
        if kind == "foreshadowing":
            row = upsert_foreshadowing(db, project_id, payload.data)
        elif kind == "causal_edge":
            row = upsert_causal_edge(db, project_id, payload.data)
        elif kind == "narrative_debt":
            row = upsert_narrative_debt(db, project_id, payload.data)
        elif kind == "character_state":
            row = record_character_state(db, project_id, payload.data)
        elif kind == "quality_metric":
            row = record_quality_metric(db, project_id, payload.data)
        else:
            raise ValidationError("不支持的治理对象类型")
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    commit_session(db)
    db.refresh(row)
    return ApiResponse.success(data={column.name: getattr(row, column.name) for column in row.__table__.columns}, message="治理项已保存")


@router.patch("/items/{item_type}/{item_id}")
def update_status(project_id: str, item_type: str, item_id: str, payload: GovernanceStatusUpdate, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    try:
        updated = get_narrative_governance_commands().update_status(
            db,
            project_id,
            item_type,
            item_id,
            payload.model_dump(exclude_unset=True),
        )
    except ValueError:
        raise ValidationError("不支持的治理对象类型")
    if not updated:
        raise NotFoundError("治理项不存在")
    return ApiResponse.success(message="状态已更新")


@router.post("/candidates")
def candidates(project_id: str, payload: GovernanceCandidateBatch, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    if payload.mode == "preview":
        return ApiResponse.success(data={"items": payload.candidates, "total": len(payload.candidates), "applied": False})
    try:
        items = apply_governance_candidates(db, project_id, payload.candidates, chapter_id=payload.chapter_id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    commit_session(db)
    return ApiResponse.success(data={"items": items, "total": len(items), "applied": True})


@router.post("/checkpoints")
def create_checkpoint(
    project_id: str,
    payload: CheckpointCreate,
    chapter_workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    db: Session = Depends(get_db),
):
    get_project_or_404(db, project_id)
    data = chapter_workspace.create_narrative_checkpoint(
        project_id,
        chapter_id=payload.chapter_id,
        label=payload.label or "",
        trigger_type=payload.trigger_type,
    )
    return ApiResponse.success(data=data)


@router.get("/checkpoints/{checkpoint_id}/diff")
def diff_checkpoint(project_id: str, checkpoint_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    try:
        data = checkpoint_diff(db, project_id, checkpoint_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    return ApiResponse.success(data=data)


@router.post("/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(project_id: str, checkpoint_id: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    try:
        row = restore_narrative_checkpoint(db, project_id, checkpoint_id)
    except ValueError as exc:
        db.rollback()
        raise NotFoundError(str(exc)) from exc
    commit_session(db)
    return ApiResponse.success(data={"id": row.id, "sequence": row.sequence, "label": row.label}, message="叙事状态已回滚")
