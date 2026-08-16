"""REST API for API-free novel creation workflow."""
from __future__ import annotations

from app.architecture.uow import commit_session

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..ai.local_cli_adapter import is_local_cli_provider
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..database.session import SessionLocal
from ..schemas.novel_creation import (
    NovelCreationStageRunResponse,
    NovelCreationStageRunStartData,
)
from ..schemas.ai_writer import MobileProviderEnvelope
from ..modules.creation.interfaces.session_dependencies import novel_creation_session_store
from ..modules.operations.interfaces.dependencies import get_operation_service
from ..services.novel_creation_claims import (
    claim_or_replay_creation_run,
    creation_idempotency_key,
    get_creation_claim_by_idempotency_key,
)
from ..services.novel_creation_consistency import (
    creation_dependency_graph,
    validate_creation_consistency,
)
from ..services.novel_creation_imports import (
    IMPORTABLE_ARTIFACTS,
    MAX_UPLOAD_BYTES,
    SUPPORTED_EXTENSIONS,
    apply_material_import,
    claim_material_import_retry,
    create_material_import as create_material_import_record,
    find_material_import_by_file,
    get_material_import_record,
    list_material_import_records,
    run_material_import,
    serialize_material_import,
)
from ..services.novel_creation_entities import (
    ENTITY_TYPES_BY_ARTIFACT,
    get_creation_entity,
    list_creation_entities,
    serialize_creation_entity,
)
from ..services.novel_creation_actions import delete_creation_entity, patch_creation_entity, restore_artifact_version
from ..services.novel_creation_retry import select_creation_retry_input
from ..services.novel_creation_versions import (
    artifact_version_diff,
    get_artifact_version,
    list_artifact_versions,
    record_artifact_version,
    serialize_artifact_version,
)
from ..services.novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    add_run_event,
    create_run,
    confirm_run,
    generation_blockers,
    get_presets,
    creation_artifact_dependencies,
    list_creation_artifacts,
    patch_creation_artifact,
    patch_session,
    serialize_creation_artifact,
    serialize_run,
    serialize_session,
    set_creation_artifact_locks,
    undo_creation_artifact,
)
from ..services.observability.run_events import classify_failure
from ..services.operation_runtime import (
    activate_operation,
    ensure_operation,
    fail_operation,
    finish_operation,
    heartbeat_loop,
    input_snapshot_hash,
    register_operation_actions,
    unregister_operation_actions,
)
from ..services.workspace.tools.novel_creation import (
    apply_novel_blueprint,
    draft_novel_blueprint,
    review_novel_blueprint,
    start_novel_creation_session,
)
from ..services.workspace.tools.novel_creation_v2 import _validate_stage, submit_novel_creation_stage
from ..services.novel_creation_task_runtime import schedule_creation_stage
from .novel_creation_support import idempotent_confirmation_response

router = APIRouter(tags=["novel-creation"])


def _operation_model_identity(model: str | None) -> tuple[str | None, str]:
    effective_model = model
    try:
        selection = LLMGateway.select_model_for_task(
            task_type="novel_creation",
            model_override=model,
        )
        effective_model = selection.model or effective_model
    except Exception:
        pass
    if not effective_model:
        return None, "model_stream"
    try:
        provider, model_name = LLMGateway.model_identity(
            effective_model,
            {"moshu_task_type": "planning"},
        )
        model_label = f"{provider}:{model_name}"
        tool_mode = "local_cli_stream" if is_local_cli_provider(provider) else "api_stream"
        return model_label, tool_mode
    except Exception:
        return effective_model, "model_stream"


def _start_inline_operation(
    db: Session,
    *,
    source_kind: str,
    title: str,
    phase: str,
    model: str | None,
    resume_url: str,
    input_value: Any,
    input_revision: int | None = None,
) -> str:
    model_source, tool_mode = _operation_model_identity(model)
    operation = ensure_operation(
        db,
        source_kind=source_kind,
        source_id=str(uuid.uuid4()),
        title=title,
        phase=phase,
        message="正在连接模型并等待首段输出",
        model_source=model_source,
        tool_mode=tool_mode,
        resume_url=resume_url,
        can_pause=False,
        can_cancel=True,
        can_retry=False,
        input_revision=input_revision,
        snapshot_hash=input_snapshot_hash(input_value),
    )
    commit_session(db)
    return operation.id


async def _run_inline_operation(
    operation_id: str,
    runner: Callable[[], Awaitable[Any]],
    *,
    success_message: str,
) -> Any:
    heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
    current_task = asyncio.current_task()
    if current_task is not None:
        register_operation_actions(operation_id, cancel=current_task.cancel)
    try:
        with activate_operation(operation_id):
            result = await runner()
        finish_operation(operation_id, message=success_message)
        return result
    except asyncio.CancelledError:
        finish_operation(operation_id, message="任务已由用户取消", status="cancelled")
        raise
    except Exception as exc:
        fail_operation(operation_id, exc, next_action="可检查模型状态后重试本轮")
        raise
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        unregister_operation_actions(operation_id)


def _inline_operation_http_error(exc: Exception) -> HTTPException:
    failure_class = classify_failure(str(exc)) or "unknown"
    next_action = {
        "quota_or_rate_limit": "请等待额度恢复，或切换到有额度的模型后重试。",
        "auth": "请到模型设置重新登录或填写凭据，测试成功后重试。",
        "timeout": "任务已保留；可继续等待模型活动，或切换更快的模型重试。",
        "empty_response": "模型没有返回有效内容，请重试本轮或切换模型。",
        "invalid_response": "模型返回格式无法解析，请重试本轮。",
    }.get(failure_class, "请检查模型状态后重试本轮。")
    return HTTPException(
        status_code=422,
        detail={
            "message": str(exc) or "模型调用失败",
            "failure_class": failure_class,
            "next_action": next_action,
        },
    )


def _resolve_mobile_creation_provider(
    db: Session,
    payload: Any,
    request: Request,
    *,
    binding_id: str,
):
    """Resolve an Android-owned key for one canonical creation operation.

    The encrypted envelope is deliberately excluded from every Pydantic dump.
    Only the decrypted in-memory provider object crosses into model execution.
    """

    if getattr(payload, "model_route", "pc") != "mobile":
        return None
    if (
        getattr(request.state, "gateway_device_platform", None) != "android"
        or not getattr(request.state, "gateway_device_id", None)
    ):
        raise ValidationError("手机模型线路只允许已配对的 Android 设备使用")

    from ..services.mobile_provider_envelope import decrypt_mobile_provider

    request_provider = decrypt_mobile_provider(
        db,
        payload.mobile_provider,
        device_id=request.state.gateway_device_id,
        project_id=binding_id,
    )
    payload.mobile_provider = None
    payload.model = f"{request_provider.provider}:{request_provider.default_model}"
    return request_provider


class NovelCreationStartRequest(BaseModel):
    mode: str = "template"
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


class NovelCreationDraftRequest(BaseModel):
    session_id: str
    execution_mode: Literal["template", "hybrid", "external_agent", "internal_llm"] = "hybrid"
    model: str | None = None
    user_brief: str = ""
    feedback: str = ""
    revision_mode: Literal["initial", "refine", "regenerate"] = "initial"
    enhance_with_llm: bool = False
    skip_questions: bool = False
    answers: dict[str, str] | None = None
    qa_history: list[dict[str, str]] | None = None
    depth: Literal["concept", "full"] = "full"


class NovelCreationReviewRequest(BaseModel):
    session_id: str
    execution_mode: Literal["template", "hybrid", "external_agent", "internal_llm"] = "hybrid"
    blueprint: Any | None = None


class NovelCreationApplyRequest(BaseModel):
    session_id: str
    blueprint_index: int = Field(0, ge=0)
    mode: Literal["manual", "auto"] = "auto"
    blueprint: Any | None = None


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


@router.post("/novel-creation/draft")
async def draft_blueprints(payload: NovelCreationDraftRequest, db: Session = Depends(get_db)):
    result = await draft_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/review")
async def review_blueprint(payload: NovelCreationReviewRequest, db: Session = Depends(get_db)):
    result = await review_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/apply")
async def apply_blueprint(
    payload: NovelCreationApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    result = await apply_novel_blueprint(db, "", payload.model_dump())
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


class NovelCreationStageRunRequest(BaseModel):
    stage: str
    model: str | None = None
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: MobileProviderEnvelope | None = Field(default=None, repr=False, exclude=True)
    use_model: bool = True
    auto_confirm: bool = False
    operation: Literal["generate", "regenerate", "refine"] = "generate"
    instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    session_patch: dict[str, Any] | None = None
    expected_revision: int | None = None
    entity_id: str | None = None
    entity_type: Literal[
        "worldbuilding", "character", "relationship", "location", "faction",
        "world_relation", "volume", "chapter_outline", "scene_outline",
    ] | None = None
    entity_count: int | None = Field(default=None, ge=1, le=20)

    @field_validator("operation", mode="before")
    @classmethod
    def normalize_legacy_operation(cls, value: Any) -> Any:
        # V2 clients used this internal name for the first concept run.
        return "generate" if value == "generate_concepts" else value

    @field_validator("instruction")
    @classmethod
    def validate_refinement_instruction(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("instruction must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_refinement_instruction(self) -> "NovelCreationStageRunRequest":
        if self.operation == "refine" and not self.instruction:
            raise ValueError("refine operation requires an instruction")
        if self.entity_id and self.entity_type:
            raise ValueError("entity_id and entity_type are mutually exclusive")
        if (self.entity_id or self.entity_type) and self.stage == "all":
            raise ValueError("entity-level generation requires one artifact stage")
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


class NovelCreationStageConfirmRequest(BaseModel):
    data: dict[str, Any] | None = None
    confirm: bool = True
    source: str = "author"
    expected_revision: int | None = None


class NovelCreationConfirmAndGenerateRequest(NovelCreationStageConfirmRequest):
    model: str | None = None
    use_model: bool = True


class NovelCreationStagePatchRequest(BaseModel):
    data: dict[str, Any]
    source: str = "author"
    expected_revision: int


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


@router.post(
    "/novel-creation/sessions/{session_id}/runs",
    response_model=ApiResponse[NovelCreationStageRunStartData],
)
async def start_creation_stage_run(
    session_id: str,
    payload: NovelCreationStageRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    store = novel_creation_session_store(db)
    session = store.session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if payload.stage not in {*STAGE_ORDER, "all"}:
        raise HTTPException(status_code=400, detail="未知立项阶段")
    if payload.entity_id:
        entity = get_creation_entity(db, payload.entity_id)
        if not entity or entity.session_id != session_id or entity.status == "deleted":
            raise HTTPException(status_code=404, detail="目标实体不存在或已删除")
        if entity.artifact_key != payload.stage:
            raise HTTPException(status_code=400, detail="目标实体不属于当前立项对象")
    if payload.entity_type and payload.entity_type not in ENTITY_TYPES_BY_ARTIFACT.get(payload.stage, frozenset()):
        raise HTTPException(status_code=400, detail="目标实体类型不属于当前立项对象")
    if payload.expected_revision is not None and int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿版本已经变化，请确认当前内容后重新生成",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        )
    request_provider = _resolve_mobile_creation_provider(
        db,
        payload,
        request,
        binding_id=session_id,
    )
    blocked_by = generation_blockers(session, payload.stage)
    if blocked_by:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "请先确认前置阶段，再生成当前内容。",
                "failure_class": "stage_blocked",
                "blocked_by": blocked_by,
                "session": serialize_session(session),
                "next_action": f"返回“{blocked_by[0]['label']}”完成确认。",
            },
        )
    existing = store.running_stage(session_id, payload.stage)
    if existing:
        return ApiResponse.success(
            data={"run": serialize_run(existing), "stream_url": f"/api/novel-creation/runs/{existing.id}/stream"},
            message="该阶段任务仍在运行，已恢复订阅",
        )
    run_request = payload.model_dump()
    if payload.session_patch:
        patch_session(session, payload.session_patch)
        run_request["session_patch"] = None
    input_revision = int(session.revision or 0)
    snapshot_hash = input_snapshot_hash(session.draft_json if isinstance(session.draft_json, dict) else {})
    request_key = creation_idempotency_key(
        session_id=session_id,
        stage=payload.stage,
        operation=payload.operation,
        request=run_request,
        input_revision=input_revision,
        input_snapshot_hash=snapshot_hash,
        explicit_key=idempotency_key,
    )
    artifact_claim_key = payload.stage
    if payload.entity_id:
        artifact_claim_key = f"{payload.stage}:entity:{payload.entity_id}"
    elif payload.entity_type:
        artifact_claim_key = f"{payload.stage}:new:{payload.entity_type}"
    claim, replayed = claim_or_replay_creation_run(
        db,
        session_id=session_id,
        artifact_key=artifact_claim_key,
        idempotency_key=request_key,
        input_revision=input_revision,
        input_snapshot_hash=snapshot_hash,
    )
    if replayed and claim.run_id:
        existing = store.run(claim.run_id)
        if existing:
            return ApiResponse.success(
                data={
                    "run": serialize_run(existing),
                    "stream_url": f"/api/novel-creation/runs/{existing.id}/stream",
                    "replayed": True,
                },
                message="已恢复同一立项任务",
            )
    run = create_run(
        db,
        session,
        payload.stage,
        run_request,
        claim_id=claim.id,
        idempotency_key=request_key,
    )
    commit_session(db)
    run_id = run.id
    schedule_creation_stage(
        run_id,
        session_id,
        run_request,
        operation_id=run.operation_id,
        request_provider=request_provider,
    )
    return ApiResponse.success(data={"run": serialize_run(run), "stream_url": f"/api/novel-creation/runs/{run_id}/stream"}, message="阶段任务已创建")


@router.get(
    "/novel-creation/runs/{run_id}",
    response_model=ApiResponse[NovelCreationStageRunResponse],
)
async def get_creation_stage_run(
    run_id: str,
    model: str | None = None,
    db: Session = Depends(get_db),
):
    run = novel_creation_session_store(db).run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="阶段任务不存在")
    from ..services.novel_creation_run_presentation import present_serialized_run

    return ApiResponse.success(data=await present_serialized_run(db, run=run, model=model))


class NovelCreationRunCardRequest(BaseModel):
    message: str = Field(default="", max_length=100_000)
    model: str | None = None


class NovelCreationRunCardResponse(BaseModel):
    run: NovelCreationStageRunResponse


@router.post(
    "/novel-creation/runs/{run_id}/card-presentation",
    response_model=ApiResponse[NovelCreationRunCardResponse],
)
async def adjudicate_creation_run_card(
    run_id: str,
    payload: NovelCreationRunCardRequest,
    db: Session = Depends(get_db),
):
    """Re-evaluate a terminal card with the selected API or local-CLI model."""
    run = novel_creation_session_store(db).run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="阶段任务不存在")
    from ..services.novel_creation_run_presentation import present_serialized_run

    return ApiResponse.success(data={"run": await present_serialized_run(
        db,
        run=run,
        model=payload.model,
        assistant_reply=payload.message,
    )})


@router.get("/novel-creation/runs/{run_id}/stream")
async def stream_creation_stage_run(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after_sequence: int = 0,
):
    try:
        initial_after = max(int(last_event_id or 0), after_sequence, 0)
    except ValueError:
        initial_after = max(after_sequence, 0)

    async def events():
        sent = initial_after
        tick = 0
        while True:
            db = SessionLocal()
            try:
                run = novel_creation_session_store(db).run(run_id)
                if not run:
                    yield "event: error\ndata: " + json.dumps({"message": "阶段任务不存在"}, ensure_ascii=False) + "\n\n"
                    return
                if tick == 0:
                    yield (
                        "id: 0\n"
                        "event: snapshot\ndata: "
                        + json.dumps(serialize_run(run, include_events=False), ensure_ascii=False)
                        + "\n\n"
                    )
                rows = list(run.events or [])
                for event in rows:
                    if event.sequence <= sent:
                        continue
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "status": event.status,
                        "message": event.message,
                        "payload": event.payload_json,
                    }
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                sent = max([int(event.sequence or 0) for event in rows] or [sent])
                if run.status in {"completed", "waiting_user", "waiting_author", "failed", "cancelled", "interrupted"}:
                    from ..services.novel_creation_run_presentation import present_serialized_run

                    terminal_run = await present_serialized_run(
                        db,
                        run=run,
                        model=run.model_source,
                    )
                    yield "event: done\ndata: " + json.dumps(terminal_run, ensure_ascii=False) + "\n\n"
                    return
            finally:
                db.close()
            tick += 1
            if tick % 20 == 0:
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class NovelCreationStageRetryRequest(BaseModel):
    use_latest_draft: bool = False
    model: str | None = None


@router.post("/novel-creation/runs/{run_id}/retry")
async def retry_creation_stage_run(
    run_id: str,
    payload: NovelCreationStageRetryRequest,
    db: Session = Depends(get_db),
):
    store = novel_creation_session_store(db)
    previous = store.run(run_id)
    if not previous:
        raise HTTPException(status_code=404, detail="阶段任务不存在")
    if previous.status not in {"failed", "cancelled", "interrupted", "superseded"}:
        raise HTTPException(status_code=409, detail="当前阶段任务不需要重试")
    session = store.session(previous.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    retry_input = select_creation_retry_input(previous, session, use_latest=payload.use_latest_draft)
    request = retry_input.request
    if payload.model:
        request["model"] = payload.model
    retry_key = creation_idempotency_key(
        session_id=session.id,
        stage=previous.stage,
        operation=str(request.get("operation") or previous.operation),
        request=request,
        input_revision=retry_input.revision,
        input_snapshot_hash=retry_input.snapshot_hash,
        explicit_key=f"retry:{previous.id}:{uuid.uuid4()}",
    )
    claim, _ = claim_or_replay_creation_run(
        db,
        session_id=session.id,
        artifact_key=previous.stage,
        idempotency_key=retry_key,
        input_revision=retry_input.revision,
        input_snapshot_hash=retry_input.snapshot_hash,
    )
    run = create_run(
        db,
        session,
        previous.stage,
        request,
        claim_id=claim.id,
        idempotency_key=retry_key,
        frozen_input_snapshot=retry_input.snapshot,
        frozen_input_revision=retry_input.revision,
    )
    run.retry_of_run_id = previous.id
    commit_session(db)
    schedule_creation_stage(run.id, session.id, request, operation_id=run.operation_id)
    return ApiResponse.success(
        data={"run": serialize_run(run), "stream_url": f"/api/novel-creation/runs/{run.id}/stream"},
        message="已创建重试任务",
    )

@router.post("/novel-creation/sessions/{session_id}/stages/{stage}/confirm")
async def confirm_creation_stage(session_id: str, stage: str, payload: NovelCreationStageConfirmRequest, db: Session = Depends(get_db)):
    store = novel_creation_session_store(db)
    session = store.session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    confirmation, replay = idempotent_confirmation_response(session, stage, data=payload.data, confirm=payload.confirm)
    if replay:
        return replay
    if payload.expected_revision is not None and int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿版本已经变化，请检查最新内容后再确认。",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        )
    result = await submit_novel_creation_stage(db, "", {
        "session_id": session_id,
        "stage": stage,
        "data": payload.data if payload.data is not None else confirmation.current_data,
        "confirm": payload.confirm,
        "source": payload.source,
        "expected_revision": payload.expected_revision,
    })
    if payload.confirm:
        producing_run = store.latest_stage_operation(session_id, stage)
        if producing_run:
            confirmed = confirm_run(db, producing_run)
            if confirmed:
                commit_session(db)
            if producing_run.operation_id:
                get_operation_service().complete_author_confirmation(producing_run.operation_id)
        # The submission result is serialized before the run changes from
        # waiting_user to completed. Re-serialize after confirmation so every
        # caller receives one coherent snapshot instead of a confirmed
        # artifact paired with a stale waiting task.
        result["data"] = serialize_session(session)
    return _tool_response(result)


@router.patch("/novel-creation/sessions/{session_id}/stages/{stage}")
async def update_creation_stage(session_id: str, stage: str, payload: NovelCreationStagePatchRequest, db: Session = Depends(get_db)):
    session = novel_creation_session_store(db).session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿版本已经变化，本地修改尚未覆盖最新版本。",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
        )
    result = await submit_novel_creation_stage(db, "", {
        "session_id": session_id,
        "stage": stage,
        "data": payload.data,
        "confirm": False,
        "source": payload.source,
        "expected_revision": payload.expected_revision,
    })
    return _tool_response(result)


@router.post("/novel-creation/sessions/{session_id}/stages/{stage}/confirm-and-generate-recommended")
async def confirm_and_generate_recommended(
    session_id: str,
    stage: str,
    payload: NovelCreationConfirmAndGenerateRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    request: Request = None,
):
    if idempotency_key:
        existing_claim = get_creation_claim_by_idempotency_key(
            db,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )
        if existing_claim and existing_claim.run_id:
            existing_run = novel_creation_session_store(db).run(existing_claim.run_id)
            existing_session = novel_creation_session_store(db).session(session_id)
            if existing_run and existing_session:
                return ApiResponse.success(
                    data={
                        "action_type": "confirm_and_generate_recommended",
                        "session": serialize_session(existing_session),
                        "run": serialize_run(existing_run),
                        "recommended_stage": existing_run.stage,
                    },
                    message="已恢复同一次确认并继续任务",
                )
    await confirm_creation_stage(session_id, stage, payload, db)
    store = novel_creation_session_store(db)
    session = store.session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    serialized = serialize_session(session)
    recommended = (serialized.get("stage_flow") or {}).get("recommended_stage")
    producing_run = store.latest_stage_operation(session_id, stage)
    if producing_run:
        add_run_event(
            db,
            producing_run,
            "confirm_and_generate_recommended",
            "completed",
            "已确认当前内容并请求生成推荐对象",
            {"action_type": "confirm_and_generate_recommended", "recommended_stage": recommended},
        )
        commit_session(db)
    if not recommended or recommended == stage or recommended not in STAGE_ORDER:
        return ApiResponse.success(
            data={
                "action_type": "confirm_and_generate_recommended",
                "session": serialize_session(session),
                "run": None,
                "recommended_stage": recommended,
            },
            message="当前内容已确认；没有需要自动生成的下一对象",
        )
    start_payload = NovelCreationStageRunRequest(
        stage=recommended,
        model=payload.model,
        use_model=payload.use_model,
        expected_revision=int(session.revision or 0),
    )
    stable_key = idempotency_key or f"confirm-next:{session_id}:{stage}:{int(session.revision or 0)}:{recommended}"
    started = await start_creation_stage_run(
        session_id,
        start_payload,
        request,
        db,
        idempotency_key=stable_key,
    )
    return ApiResponse.success(
        data={
            "action_type": "confirm_and_generate_recommended",
            "session": serialize_session(session),
            "run": started.data.get("run") if isinstance(started.data, dict) else None,
            "recommended_stage": recommended,
        },
        message=f"已确认当前内容，并开始生成{STAGE_LABELS.get(recommended, recommended)}",
    )


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


class SystemChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1_000_000)
    model: str | None = None
    context: dict[str, Any] | None = None  # {blueprints, sessionId, brief, importedFiles, history}


class AssistantInputRouteRequest(BaseModel):
    source_name: str = Field(default="聊天长文本.txt", min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=5_000_000)
    source_kind: Literal["long_text", "attachment"] = "attachment"
    user_instruction: str = Field(default="", max_length=1_000_000)
    clarification_question: str = Field(default="", max_length=500)
    clarification_answer: str = Field(default="", max_length=20_000)
    clarification_already_asked: bool = False
    clarification_history: list[dict[str, Any]] = Field(default_factory=list)
    context_scope: Literal["system", "creation", "project"] = "system"
    active_project_id: str = Field(default="", max_length=36)
    creation_session_id: str = Field(default="", max_length=36)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
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
    clarification_question: str = Form(default=""),
    clarification_answer: str = Form(default=""),
    clarification_already_asked: bool = Form(default=False),
    clarification_history: str = Form(default="[]"),
    context_scope: Literal["system", "creation", "project"] = Form(default="system"),
    active_project_id: str = Form(default=""),
    creation_session_id: str = Form(default=""),
    history: str = Form(default="[]"),
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
        parsed_history = json.loads(history or "[]")
    except (TypeError, ValueError):
        parsed_history = []
    try:
        parsed_clarification_history = json.loads(clarification_history or "[]")
    except (TypeError, ValueError):
        parsed_clarification_history = []
    result = await classify_assistant_data_input(
        source_name=filename,
        source_text=source_text,
        source_kind="attachment",
        user_instruction=user_instruction,
        clarification_question=clarification_question,
        clarification_answer=clarification_answer,
        clarification_already_asked=clarification_already_asked,
        clarification_history=(
            parsed_clarification_history
            if isinstance(parsed_clarification_history, list)
            else []
        ),
        context_scope=context_scope,
        active_project_id=active_project_id,
        creation_session_id=creation_session_id,
        history=parsed_history if isinstance(parsed_history, list) else [],
        model=model,
    )
    return ApiResponse.success(data=result, message="文件内容与处理意图已判断")


@router.post("/novel-creation/system-chat")
async def system_chat(payload: SystemChatRequest, db: Session = Depends(get_db)):
    """General conversation endpoint for system assistant without project context."""
    from app.services.workspace.tools.novel_creation import system_chat_completion

    operation_id = _start_inline_operation(
        db,
        source_kind="system_chat",
        title="司命对话",
        phase="generating_reply",
        model=payload.model,
        resume_url="/gui",
        input_value={"message": payload.message, "context": payload.context or {}, "model": payload.model},
    )

    async def run_chat() -> dict[str, Any]:
        return await system_chat_completion(
            message=payload.message,
            context=payload.context or {},
            model=payload.model,
        )

    try:
        result = await _run_inline_operation(operation_id, run_chat, success_message="司命已返回回复")
    except HTTPException:
        raise
    except Exception as exc:
        raise _inline_operation_http_error(exc) from exc
    return ApiResponse.success(data=result)


class CreationConversationCommandRequest(BaseModel):
    session_id: str
    stage: str
    instruction: str | None = None
    model: str | None = None
    expected_revision: int | None = None
    entity_type: Literal[
        "worldbuilding", "character", "relationship", "location", "faction",
        "world_relation", "volume", "chapter_outline", "scene_outline",
    ] | None = None
    entity_count: int | None = Field(default=None, ge=1, le=20)
    action: Literal["generate_artifact", "refine_artifact", "open_editor"] = "refine_artifact"

    @model_validator(mode="after")
    def validate_entity_target(self) -> "CreationConversationCommandRequest":
        if self.entity_type and self.entity_type not in ENTITY_TYPES_BY_ARTIFACT.get(self.stage, frozenset()):
            raise ValueError("目标实体类型不属于当前立项对象")
        if self.entity_type and self.action != "generate_artifact":
            raise ValueError("新增实体只能使用 generate_artifact")
        return self


class CreationAgentRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=1_000_000)
    model: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: MobileProviderEnvelope | None = Field(default=None, repr=False, exclude=True)
    local_cli_permission_grant: Literal["chat_only", "creation_agent_once"] = "chat_only"
    local_cli_read_permission_grant: Literal["none", "read_once"] = "none"
    local_cli_read_paths: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def require_mobile_provider_envelope(self) -> "CreationAgentRequest":
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


@router.post("/novel-creation/agent-turn")
async def creation_agent_turn(
    payload: CreationAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session = novel_creation_session_store(db).session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    from ..services.novel_creation_agent import run_creation_agent

    request_provider = _resolve_mobile_creation_provider(
        db,
        payload,
        request,
        binding_id=session.id,
    )

    async def run_agent() -> dict[str, Any]:
        return await run_creation_agent(
            db,
            session=session,
            message=payload.message,
            model=payload.model,
            history=payload.history,
            local_cli_write_granted=payload.local_cli_permission_grant == "creation_agent_once",
            local_cli_read_paths=(
                list(payload.local_cli_read_paths)
                if payload.local_cli_read_permission_grant == "read_once" else []
            ),
        )

    if request_provider is None:
        result = await run_agent()
    else:
        from ..modules.model_runtime.application.request_override import use_request_provider
        with use_request_provider(request_provider):
            result = await run_agent()
    return ApiResponse.success(data=result)


@router.post("/novel-creation/conversation-command")
async def creation_conversation_command(
    payload: CreationConversationCommandRequest,
    db: Session = Depends(get_db),
):
    if payload.stage not in {*STAGE_ORDER, "all"}:
        raise HTTPException(status_code=400, detail="未知立项阶段")
    store = novel_creation_session_store(db)
    session = store.session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if payload.expected_revision is not None and int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(status_code=409, detail="立项草稿版本已经变化")
    if payload.action == "open_editor":
        return ApiResponse.success(data={
            "ui_directive": {"navigate": True, "url": f"/novel-creation?session={session.id}&stage={payload.stage}"},
            "summary": f"已打开{STAGE_LABELS.get(payload.stage, payload.stage)}完整编辑器",
        })
    operation = "refine" if payload.action == "refine_artifact" else "generate"
    entity_id: str | None = None
    if operation == "refine" and payload.stage in {"characters", "locations", "macro_outline", "opening_outline", "world_style"}:
        candidates = []
        for item in list_creation_entities(session, artifact=payload.stage):
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            labels = {
                str(data.get("name") or "").strip(),
                str(data.get("title") or "").strip(),
                str(data.get("role") or data.get("role_type") or "").strip(),
            }
            labels.discard("")
            if any(label in payload.instruction for label in labels):
                candidates.append(item)
        if len(candidates) == 1:
            entity_id = str(candidates[0]["id"])
    request = {
        "session_id": session.id,
        "stage": payload.stage,
        "operation": operation,
        "instruction": payload.instruction,
        "model": payload.model,
        "expected_revision": int(session.revision or 0),
        "entity_id": entity_id,
        "entity_type": payload.entity_type,
        "entity_count": payload.entity_count,
    }
    snapshot_hash = input_snapshot_hash(session.draft_json if isinstance(session.draft_json, dict) else {})
    command_key = creation_idempotency_key(
        session_id=session.id,
        stage=payload.stage,
        operation=operation,
        request=request,
        input_revision=int(session.revision or 0),
        input_snapshot_hash=snapshot_hash,
    )
    artifact_claim_key = (
        f"{payload.stage}:entity:{entity_id}"
        if entity_id else
        f"{payload.stage}:new:{payload.entity_type}"
        if payload.entity_type else
        payload.stage
    )
    claim, replayed = claim_or_replay_creation_run(
        db,
        session_id=session.id,
        artifact_key=artifact_claim_key,
        idempotency_key=command_key,
        input_revision=int(session.revision or 0),
        input_snapshot_hash=snapshot_hash,
    )
    if replayed and claim.run_id:
        run = store.run(claim.run_id)
        if run:
            return ApiResponse.success(data={
                "run": serialize_run(run),
                "ui_directive": {"navigate": False},
                "summary": f"正在继续{STAGE_LABELS.get(payload.stage, payload.stage)}任务",
            })
    run = create_run(db, session, payload.stage, request, claim_id=claim.id, idempotency_key=command_key)
    commit_session(db)
    schedule_creation_stage(run.id, session.id, request, operation_id=run.operation_id)
    return ApiResponse.success(data={
        "run": serialize_run(run),
        "ui_directive": {"navigate": False},
        "summary": (
            f"已开始定向调整{candidates[0]['data'].get('name') or candidates[0]['data'].get('title')}，其他对象会保持不变。"
            if entity_id else
            f"已开始按你的描述新增{ {'character': '角色', 'relationship': '人物关系', 'location': '地点', 'faction': '势力', 'volume': '分卷', 'chapter_outline': '章节细纲', 'scene_outline': '场景细纲', 'worldbuilding': '世界设定', 'world_relation': '世界关系'}.get(payload.entity_type, '立项对象') }，数量由本次对话要求决定，现有内容会保持不变。"
            if payload.entity_type else
            f"已开始{STAGE_LABELS.get(payload.stage, payload.stage)}{('调整' if operation == 'refine' else '生成')}，你可以继续在对话中补充要求。"
        ),
    })


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
    import os

    root = content_root()
    imported_dir = root / ".imported"
    imported_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', payload.filename)
    safe_name = safe_name.strip(' .-')[:200]

    # Add timestamp to avoid conflicts
    from datetime import datetime
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
    from app.services.content_store import content_root
    from datetime import datetime

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
