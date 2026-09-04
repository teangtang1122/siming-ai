"""Auxiliary CRUD, input-routing, and import routes for novel creation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.creation.interfaces.session_dependencies import novel_creation_session_store
from ..services.novel_creation_actions import (
    delete_creation_entity,
    patch_creation_entity,
    restore_artifact_version,
)
from ..services.novel_creation_consistency import (
    creation_dependency_graph,
    validate_creation_consistency,
)
from ..services.novel_creation_entities import (
    get_creation_entity,
    list_creation_entities,
    serialize_creation_entity,
)
from ..services.novel_creation_imports import (
    IMPORTABLE_ARTIFACTS,
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
    apply_material_import,
    claim_material_import_retry,
    find_material_import_by_file,
    get_material_import_record,
    list_material_import_records,
    run_material_import,
    serialize_material_import,
)
from ..services.novel_creation_imports import (
    create_material_import as create_material_import_record,
)
from ..services.novel_creation_versions import (
    artifact_version_diff,
    get_artifact_version,
    list_artifact_versions,
    record_artifact_version,
    serialize_artifact_version,
)
from ..services.novel_creation_workspace import (
    STAGE_LABELS,
    creation_artifact_dependencies,
    get_presets,
    list_creation_artifacts,
    patch_creation_artifact,
    patch_session,
    serialize_creation_artifact,
    serialize_session,
    set_creation_artifact_locks,
    undo_creation_artifact,
)
from ..services.operation_runtime import (
    ensure_operation,
    register_operation_actions,
)
from ..services.workspace.tools.novel_creation import (
    finalize_creation_session,
    start_novel_creation_session,
)
from ..services.workspace.tools.novel_creation_v2 import _validate_stage
from .novel_creation_operation_helpers import (
    operation_model_identity as _operation_model_identity,
)

router = APIRouter(tags=["novel-creation"])


class NovelCreationStartRequest(BaseModel):
    mode: Literal["internal_llm", "external_agent"] = "internal_llm"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""
    preset_id: str = "free"
    theme_id: str = ""
    target_words: int = Field(600000, ge=10000, le=10000000)
    target_chapters: int = Field(240, ge=1, le=5000)
    world_tone: str = ""
    story_structure: str = ""
    pacing: str = ""
    writing_style: str = ""
    special_requirements: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    author_overrides: dict[str, Any] = Field(default_factory=dict)
    creation_mode: Literal["author_led", "explore"] = "explore"
    author_brief: str = Field(default="", max_length=5000)
    author_outline: str = Field(default="", max_length=20000)
    locked_requirements: list[str] = Field(default_factory=list, max_length=100)


class NovelCreationFinalizeRequest(BaseModel):
    session_id: str


def _tool_response(result: dict[str, Any]) -> ApiResponse:
    status = result.get("status")
    detail = result.get("detail") or status or "success"
    if status not in ("ok", "need_clarification", "need_model"):
        raise HTTPException(status_code=400, detail=detail)
    return ApiResponse.success(data=result.get("data"), message=detail)


@router.post("/novel-creation/start")
async def start_creation(payload: NovelCreationStartRequest, db: Session = Depends(get_db)):
    result = await start_novel_creation_session(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/finalize")
async def finalize_creation(
    payload: NovelCreationFinalizeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    result = await finalize_creation_session(db, "", payload.model_dump())
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    project_id = str(data.get("project_id") or "")
    if (
        result.get("status") == "ok"
        and project_id
        and getattr(request.state, "gateway_device_id", None)
        and getattr(request.state, "gateway_device_platform", None) == "android"
    ):
        # Match /projects: a work created by a paired phone is immediately part
        # of that phone's explicit sync set, including every archived artifact.
        from ..modules.gateway.infrastructure.service import GatewayService

        GatewayService(db).enable_project(project_id)
    return _tool_response(result)


class NovelCreationSessionPatchRequest(BaseModel):
    form: dict[str, Any] | None = None
    selected_concept_id: str | None = None
    quick_mode: bool | None = None
    creation_mode: Literal["author_led", "explore"] | None = None
    author_brief: str | None = Field(default=None, max_length=5000)
    author_outline: str | None = Field(default=None, max_length=20000)
    locked_requirements: list[str] | None = Field(default=None, max_length=100)
    expected_revision: int | None = None


class NovelCreationArtifactPatchRequest(BaseModel):
    changes: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    source: str = "author"
    expected_revision: int
    allow_incomplete: bool = False


class NovelCreationArtifactLockRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)
    expected_revision: int


class NovelCreationArtifactUndoRequest(BaseModel):
    expected_revision: int


class NovelCreationArtifactRestoreRequest(BaseModel):
    expected_revision: int


class NovelCreationEntityPatchRequest(BaseModel):
    expected_revision: int
    changes: list[dict[str, Any]] = Field(min_length=1)


class NovelCreationEntityDeleteRequest(BaseModel):
    expected_revision: int


@router.get("/novel-creation/presets")
async def novel_creation_presets():
    return ApiResponse.success(data=get_presets())


@router.get("/novel-creation/sessions")
async def list_creation_sessions(
    include_completed: bool = False,
    project_id: str | None = None,
    db: Session = Depends(get_db),
):
    sessions = novel_creation_session_store(db).sessions(
        include_completed=include_completed or bool(project_id),
        limit=30,
    )
    # Creation sessions intentionally keep durable project identifiers rather
    # than foreign keys, so deleting a work does not erase its planning audit
    # trail. User-facing lists must omit that orphaned context entirely.
    def linked_project_ids(item) -> set[str]:
        return {
            str(project_ref).strip()
            for project_ref in (item.created_project_id, item.source_project_id)
            if str(project_ref or "").strip()
        }

    project_refs = {
        project_ref
        for item in sessions
        for project_ref in linked_project_ids(item)
    }
    if project_refs:
        from ..core.db_helpers import existing_project_ids

        live_project_ids = existing_project_ids(db, project_refs)
        sessions = [
            item for item in sessions
            if linked_project_ids(item).issubset(live_project_ids)
        ]
    if project_id:
        sessions = [
            item for item in sessions
            if item.created_project_id == project_id or item.source_project_id == project_id
        ]
    return ApiResponse.success(data={"sessions": [serialize_session(item, include_runs=False) for item in sessions]})


@router.get("/novel-creation/sessions/{session_id}")
async def get_creation_session(session_id: str, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    return ApiResponse.success(data=serialize_session(session))


@router.get("/novel-creation/sessions/{session_id}/artifacts")
async def get_creation_artifacts(session_id: str, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    return ApiResponse.success(data={
        "session_id": session.id,
        "revision": int(session.revision or 0),
        "artifacts": list_creation_artifacts(session),
    })


@router.get("/novel-creation/sessions/{session_id}/dependency-graph")
async def get_creation_dependency_graph(session_id: str, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    result = creation_dependency_graph(session)
    commit_session(db)
    return ApiResponse.success(data=result)


@router.post("/novel-creation/sessions/{session_id}/validate-consistency")
async def validate_creation_session_consistency(
    session_id: str,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    result = validate_creation_consistency(session)
    commit_session(db)
    return ApiResponse.success(
        data=result,
        message="一致性检查通过" if result["valid"] else "发现需要处理的一致性问题",
    )


@router.get("/novel-creation/sessions/{session_id}/artifacts/{stage}")
async def get_creation_artifact(session_id: str, stage: str, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    try:
        return ApiResponse.success(data=serialize_creation_artifact(session, stage))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/novel-creation/sessions/{session_id}/artifacts/{stage}/dependencies")
async def get_creation_dependencies(session_id: str, stage: str, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    try:
        return ApiResponse.success(data=creation_artifact_dependencies(session, stage))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/novel-creation/sessions/{session_id}/artifacts/{stage}/versions")
async def get_creation_artifact_versions(
    session_id: str,
    stage: str,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    try:
        artifact = serialize_creation_artifact(session, stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(artifact.get("data"), dict):
        record_artifact_version(
            session,
            stage,
            artifact["data"],
            revision=int(session.revision or 0),
            status=artifact["status"],
            source=artifact["source"],
            change_type="legacy_baseline",
        )
        commit_session(db)
    versions = list_artifact_versions(db, session_id=session_id, artifact=stage, limit=limit)
    return ApiResponse.success(data={
        "session_id": session_id,
        "artifact": stage,
        "revision": int(session.revision or 0),
        "versions": [serialize_artifact_version(item) for item in versions],
    })


@router.get("/novel-creation/artifact-versions/{version_id}")
async def get_creation_artifact_version(
    version_id: str,
    against_version_id: str | None = None,
    db: Session = Depends(get_db),
):
    version = get_artifact_version(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="立项版本不存在")
    try:
        data = artifact_version_diff(db, version, against_version_id=against_version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data["snapshot"] = version.snapshot_json
    return ApiResponse.success(data=data)


@router.post("/novel-creation/artifact-versions/{version_id}/restore")
async def restore_creation_artifact_version(
    version_id: str,
    payload: NovelCreationArtifactRestoreRequest,
    db: Session = Depends(get_db),
):
    version = get_artifact_version(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="立项版本不存在")
    session = novel_creation_session_store(db).session(version.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    try:
        result = restore_artifact_version(session, version, expected_revision=payload.expected_revision)
        commit_session(db)
        return ApiResponse.success(data=result, message="已恢复所选版本，原内容仍保留在版本历史中")
    except RuntimeError as exc:
        db.rollback()
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="立项数据已变化，请刷新版本历史后重试") from exc
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/novel-creation/sessions/{session_id}/entities")
async def get_creation_entities(
    session_id: str,
    artifact: str | None = None,
    entity_type: str | None = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    entities = list_creation_entities(
        session,
        artifact=artifact,
        entity_type=entity_type,
        include_deleted=include_deleted,
    )
    commit_session(db)
    return ApiResponse.success(data={
        "session_id": session_id,
        "revision": int(session.revision or 0),
        "entities": entities,
    })


@router.get("/novel-creation/entities/{entity_id}")
async def get_creation_entity_endpoint(entity_id: str, db: Session = Depends(get_db)):
    entity = get_creation_entity(db, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="立项实体不存在")
    return ApiResponse.success(data=serialize_creation_entity(entity))


@router.patch("/novel-creation/entities/{entity_id}")
async def patch_creation_entity_endpoint(
    entity_id: str,
    payload: NovelCreationEntityPatchRequest,
    db: Session = Depends(get_db),
):
    entity = get_creation_entity(db, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="立项实体不存在")
    session = novel_creation_session_store(db).session(entity.session_id)
    try:
        result = patch_creation_entity(
            session,
            entity,
            payload.changes,
            expected_revision=payload.expected_revision,
        )
        commit_session(db)
        return ApiResponse.success(data=result, message="立项实体已更新")
    except RuntimeError as exc:
        db.rollback()
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="立项数据已变化，请刷新实体后重试") from exc
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/novel-creation/entities/{entity_id}")
async def delete_creation_entity_endpoint(
    entity_id: str,
    payload: NovelCreationEntityDeleteRequest,
    db: Session = Depends(get_db),
):
    entity = get_creation_entity(db, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="立项实体不存在")
    session = novel_creation_session_store(db).session(entity.session_id)
    try:
        result = delete_creation_entity(
            session,
            entity,
            expected_revision=payload.expected_revision,
        )
        commit_session(db)
        return ApiResponse.success(data=result, message="立项实体已删除，可通过版本历史恢复")
    except RuntimeError as exc:
        db.rollback()
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="立项数据已变化，请刷新实体后重试") from exc
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/novel-creation/sessions/{session_id}")
async def update_creation_session(session_id: str, payload: NovelCreationSessionPatchRequest, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if payload.expected_revision is not None and int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿已在其他位置更新，本地修改尚未覆盖服务器版本",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        )
    try:
        patch_session(session, payload.model_dump(exclude_none=True, exclude={"expected_revision"}))
        commit_session(db)
        return ApiResponse.success(data=serialize_session(session), message="立项草稿已保存")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/novel-creation/sessions/{session_id}")
async def delete_creation_session(session_id: str, db: Session = Depends(get_db)):
    store = novel_creation_session_store(db)
    session = store.session(session_id)
    if not session:
        return ApiResponse.success(data={"deleted": False})
    if session.created_project_id:
        raise HTTPException(status_code=409, detail="该立项已创建正式作品，不能删除会话记录")
    store.delete(session)
    commit_session(db)
    return ApiResponse.success(data={"deleted": True})


def _require_creation_revision(session: Any, expected_revision: int) -> None:
    if int(session.revision or 0) != expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿版本已经变化，本次操作未写入。",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        )


@router.patch("/novel-creation/sessions/{session_id}/artifacts/{stage}")
async def patch_creation_artifact_endpoint(
    session_id: str,
    stage: str,
    payload: NovelCreationArtifactPatchRequest,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    _require_creation_revision(session, payload.expected_revision)
    try:
        result = patch_creation_artifact(
            session,
            stage,
            payload.changes,
            source=payload.source,
            validator=None if payload.allow_incomplete else _validate_stage,
        )
        commit_session(db)
        return ApiResponse.success(data=result, message=f"{STAGE_LABELS[stage]}已局部更新")
    except (KeyError, TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/novel-creation/sessions/{session_id}/artifacts/{stage}/locks")
async def lock_creation_artifact_fields(
    session_id: str,
    stage: str,
    payload: NovelCreationArtifactLockRequest,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    _require_creation_revision(session, payload.expected_revision)
    try:
        artifact = set_creation_artifact_locks(session, stage, payload.paths, locked=True)
        commit_session(db)
        return ApiResponse.success(data=artifact, message="字段已锁定")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/novel-creation/sessions/{session_id}/artifacts/{stage}/locks")
async def unlock_creation_artifact_fields(
    session_id: str,
    stage: str,
    payload: NovelCreationArtifactLockRequest,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    _require_creation_revision(session, payload.expected_revision)
    try:
        artifact = set_creation_artifact_locks(session, stage, payload.paths, locked=False)
        commit_session(db)
        return ApiResponse.success(data=artifact, message="字段已解锁")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/novel-creation/sessions/{session_id}/artifacts/{stage}/undo")
async def undo_creation_artifact_endpoint(
    session_id: str,
    stage: str,
    payload: NovelCreationArtifactUndoRequest,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    _require_creation_revision(session, payload.expected_revision)
    try:
        result = undo_creation_artifact(session, stage)
        commit_session(db)
        return ApiResponse.success(data=result, message=f"已撤销{STAGE_LABELS[stage]}的最近一次修改")
    except (KeyError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AssistantInputRouteRequest(BaseModel):
    source_name: str = Field(default="聊天长文本.txt", min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=5_000_000)
    source_kind: Literal["long_text", "attachment"] = "attachment"
    user_instruction: str = Field(default="", max_length=1_000_000)
    clarification_history: list[dict[str, Any]] = Field(default_factory=list)
    context_scope: Literal["creation", "project"] = "creation"
    active_project_id: str = Field(default="", max_length=36)
    creation_session_id: str = Field(default="", max_length=36)
    model: str | None = None


@router.post("/novel-creation/assistant-input/route")
async def route_assistant_input(payload: AssistantInputRouteRequest):
    """Let the selected model interpret chat instructions and document content together."""
    from ..services.assistant_input_routing import classify_assistant_data_input

    result = await classify_assistant_data_input(**payload.model_dump())
    return ApiResponse.success(data=result, message="输入处理意图已判断")


@router.post("/novel-creation/assistant-input/route-file")
async def route_assistant_input_file(
    file: UploadFile = File(...),
    user_instruction: str = Form(default=""),
    clarification_history: str = Form(default="[]"),
    context_scope: Literal["creation", "project"] = Form(default="creation"),
    active_project_id: str = Form(default=""),
    creation_session_id: str = Form(default=""),
    model: str | None = Form(default=None),
):
    """Parse the real uploaded binary before asking the model to route it."""
    from ..services.assistant_input_routing import classify_assistant_data_input
    from ..services.novel_creation_imports import parse_creation_material

    filename = (file.filename or "").strip()
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        source_text, _extension = parse_creation_material(filename, raw)
    except ValueError as exc:
        status_code = 413 if "25MB" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    try:
        parsed_clarification_history = json.loads(clarification_history or "[]")
    except (TypeError, ValueError):
        parsed_clarification_history = []
    result = await classify_assistant_data_input(
        source_name=filename,
        source_text=source_text,
        source_kind="attachment",
        user_instruction=user_instruction,
        clarification_history=(
            parsed_clarification_history
            if isinstance(parsed_clarification_history, list)
            else []
        ),
        context_scope=context_scope,
        active_project_id=active_project_id,
        creation_session_id=creation_session_id,
        model=model,
    )
    return ApiResponse.success(data=result, message="文件内容与处理意图已判断")


class SaveImportedFileRequest(BaseModel):
    filename: str
    content: str


class CreationImportApplyRequest(BaseModel):
    selected_artifacts: list[str] = Field(default_factory=list)
    strategy: Literal["merge", "overwrite_unconfirmed", "skip_conflicts"] = "merge"
    expected_revision: int

    @field_validator("selected_artifacts")
    @classmethod
    def validate_artifacts(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(IMPORTABLE_ARTIFACTS))
        if unknown:
            raise ValueError("不支持的导入对象：" + "、".join(unknown))
        return list(dict.fromkeys(value))


def _launch_material_import(import_run: Any, model: str | None) -> None:
    task = asyncio.create_task(run_material_import(import_run.id, model))
    if import_run.operation_id:
        register_operation_actions(import_run.operation_id, cancel=task.cancel)


@router.post("/novel-creation/sessions/{session_id}/imports")
async def create_material_import(
    session_id: str,
    file: UploadFile = File(...),
    model: str | None = Form(default=None),
    source_message_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项会话不存在")
    filename = (file.filename or "").strip()
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 txt、md、docx 和 json 文件")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25MB 上限")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        import_run, replayed = create_material_import_record(
            db,
            session,
            filename=filename,
            raw=raw,
            model=model,
            source_message_id=source_message_id,
            media_type=file.content_type or extension,
        )
        commit_session(db)
    except IntegrityError:
        db.rollback()
        existing = find_material_import_by_file(db, session_id=session_id, file_sha256=digest)
        if existing:
            return ApiResponse.success(data=serialize_material_import(existing), message="已恢复同一文件的导入任务")
        raise
    if replayed:
        return ApiResponse.success(data=serialize_material_import(import_run), message="已恢复同一文件的导入任务")
    _launch_material_import(import_run, model)
    return ApiResponse.success(data=serialize_material_import(import_run), message="文件已保存，导入任务已开始")


@router.get("/novel-creation/sessions/{session_id}/imports")
async def list_material_imports(session_id: str, db: Session = Depends(get_db)):
    if not novel_creation_session_store(db).session(session_id):
        raise HTTPException(status_code=404, detail="立项会话不存在")
    rows = list_material_import_records(db, session_id)
    return ApiResponse.success(data={"imports": [serialize_material_import(row, include_preview=False) for row in rows]})


@router.get("/novel-creation/imports/{import_id}")
async def get_material_import(import_id: str, db: Session = Depends(get_db)):
    import_run = get_material_import_record(db, import_id)
    if not import_run:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return ApiResponse.success(data=serialize_material_import(import_run))


@router.post("/novel-creation/imports/{import_id}/retry")
async def retry_material_import(
    import_id: str,
    model: str | None = None,
    db: Session = Depends(get_db),
):
    import_run = get_material_import_record(db, import_id)
    if not import_run:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    if import_run.status in {"queued", "running", "waiting_user", "completed"}:
        return ApiResponse.success(data=serialize_material_import(import_run), message="已恢复现有导入状态")
    claimed = claim_material_import_retry(db, import_id)
    if not claimed:
        db.rollback()
        current = get_material_import_record(db, import_id)
        if not current:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        return ApiResponse.success(data=serialize_material_import(current), message="已恢复现有导入状态")
    commit_session(db)
    import_run = get_material_import_record(db, import_id)
    session = novel_creation_session_store(db).session(import_run.session_id)
    model_source, tool_mode = _operation_model_identity(model)
    try:
        operation = ensure_operation(
            db,
            source_kind="novel_creation_import",
            source_id=import_run.id,
            title=f"重试导入 · {import_run.filename}",
            status="queued",
            phase="resuming_checkpoint",
            message="正在从已保存的分块检查点继续",
            model_source=model_source,
            tool_mode=tool_mode if model else "deterministic_import",
            resume_url=f"/gui?creationSession={import_run.session_id}&import={import_run.id}",
            can_cancel=True,
            can_retry=True,
            progress_mode="steps",
            progress_current=int(import_run.processed_chunks or 0),
            progress_total=int(import_run.chunk_count or 0) or None,
            input_revision=int(session.revision or 0) if session else import_run.input_revision,
            snapshot_hash=import_run.file_sha256,
        )
    except Exception as exc:
        import_run.status = "failed"
        import_run.error = f"无法恢复 Operation：{exc}"[:4000]
        commit_session(db)
        raise
    import_run.operation_id = operation.id
    import_run.status = "queued"
    import_run.updated_at = datetime.utcnow()
    commit_session(db)
    _launch_material_import(import_run, model)
    return ApiResponse.success(data=serialize_material_import(import_run), message="已从分块检查点重试")


@router.post("/novel-creation/imports/{import_id}/apply")
async def apply_material_import_endpoint(
    import_id: str,
    payload: CreationImportApplyRequest,
    db: Session = Depends(get_db),
):
    import_run = get_material_import_record(db, import_id)
    if not import_run:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    try:
        result = apply_material_import(
            db,
            import_run,
            selected_artifacts=payload.selected_artifacts,
            strategy=payload.strategy,
            expected_revision=payload.expected_revision,
        )
    except RuntimeError as exc:
        if str(exc) == "revision_conflict":
            raise HTTPException(status_code=409, detail="立项数据已变化，请刷新预览后再应用") from exc
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.success(data=result, message="已按选择写入立项数据")


@router.post("/novel-creation/save-imported-file")
async def save_imported_file(payload: SaveImportedFileRequest):
    """Save an imported file to the working directory for LLM CLI access."""
    from app.services.content_store import content_root

    root = content_root()
    imported_dir = root / ".imported"
    imported_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', payload.filename)
    safe_name = safe_name.strip(' .-')[:200]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_parts = safe_name.rsplit('.', 1)
    if len(name_parts) == 2:
        final_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
    else:
        final_name = f"{safe_name}_{timestamp}"

    file_path = imported_dir / final_name
    file_path.write_text(payload.content, encoding='utf-8')

    return ApiResponse.success(data={
        "path": str(file_path),
        "filename": final_name,
        "size": len(payload.content),
    })


@router.get("/novel-creation/imported-files")
async def list_imported_files():
    """List all imported files in the working directory."""
    from datetime import datetime

    from app.services.content_store import content_root

    root = content_root()
    imported_dir = root / ".imported"
    if not imported_dir.exists():
        return ApiResponse.success(data={"files": [], "directory": str(imported_dir)})

    files = []
    for f in sorted(imported_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            files.append({
                "filename": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })

    return ApiResponse.success(data={"files": files, "directory": str(imported_dir)})


@router.get("/novel-creation/imported-files/{filename}")
async def read_imported_file(filename: str):
    """Read the content of a specific imported file."""
    from app.services.content_store import content_root

    root = content_root()
    imported_dir = root / ".imported"
    file_path = imported_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    # Security: prevent path traversal
    if not file_path.resolve().is_relative_to(imported_dir.resolve()):
        raise HTTPException(status_code=403, detail="访问被拒绝")

    content = file_path.read_text(encoding='utf-8')
    return ApiResponse.success(data={
        "filename": filename,
        "content": content,
        "size": len(content),
        "path": str(file_path),
    })
