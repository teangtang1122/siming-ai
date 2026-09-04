"""REST API for API-free novel creation workflow."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import SessionLocal, get_db
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)
from ..modules.creation.interfaces.session_dependencies import novel_creation_session_store
from ..modules.operations.interfaces.dependencies import get_operation_service
from ..schemas.ai_writer import MobileProviderEnvelope
from ..schemas.novel_creation import (
    NovelCreationStageRunResponse,
    NovelCreationStageRunStartData,
)
from ..services.conversation_context import ReferenceContext
from ..services.creation_agent_turn_runtime import (
    CreationAgentTurnInput,
    CreationTurnScopeError,
    creation_agent_conversation,
    creation_agent_turn_stream,
    produce_creation_agent_turn,
)
from ..services.novel_creation_claims import (
    claim_or_replay_creation_run,
    creation_idempotency_key,
    get_creation_claim_by_idempotency_key,
)
from ..services.novel_creation_entities import (
    ENTITY_TYPES_BY_ARTIFACT,
    get_creation_entity,
)
from ..services.novel_creation_retry import select_creation_retry_input
from ..services.novel_creation_task_runtime import schedule_creation_stage
from ..services.novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    add_run_event,
    confirm_run,
    create_run,
    patch_session,
    serialize_run,
    serialize_session,
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
from ..services.workspace.tools.novel_creation_v2 import save_creation_artifact
from . import novel_creation_aux_routes as _novel_creation_aux_routes
from . import novel_creation_context as _novel_creation_context
from .novel_creation_operation_helpers import (
    operation_model_identity as _operation_model_identity,
)
from .novel_creation_support import idempotent_confirmation_response

router = APIRouter(tags=["novel-creation"])
router.include_router(_novel_creation_aux_routes.router)
router.include_router(_novel_creation_context.router)
_tool_response = _novel_creation_aux_routes._tool_response


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
    context_entity_ids: list[str] = Field(default_factory=list, max_length=24)
    context_artifacts: list[str] = Field(default_factory=list, max_length=6)

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
    expected_revision: int | None = None


class NovelCreationConfirmAndGenerateRequest(NovelCreationStageConfirmRequest):
    model: str | None = None
    use_model: bool = True


class NovelCreationStagePatchRequest(BaseModel):
    data: dict[str, Any]
    expected_revision: int


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
        operation_progress_signature: tuple[Any, ...] | None = None
        tick = 0
        while True:
            db = SessionLocal()
            try:
                run = novel_creation_session_store(db).run(run_id)
                if not run:
                    yield (
                        "event: error\ndata: "
                        + json.dumps({"message": "阶段任务不存在"}, ensure_ascii=False)
                        + "\n\n"
                    )
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
                    yield (
                        f"id: {event.sequence}\nevent: {event.event_type}\ndata: "
                        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                sent = max([int(event.sequence or 0) for event in rows] or [sent])
                operation = (
                    get_operation_service().get(run.operation_id, include_events=False)
                    if run.operation_id
                    else None
                )
                metrics = (
                    operation.get("process_metrics")
                    if operation and isinstance(operation.get("process_metrics"), dict)
                    else {}
                )
                if metrics.get("kind") == "model_output":
                    signature = (
                        metrics.get("output_chars"),
                        metrics.get("output_preview"),
                        metrics.get("attempt"),
                    )
                    if signature != operation_progress_signature:
                        operation_progress_signature = signature
                        payload = {
                            "event_type": "model_output",
                            "status": run.status,
                            "message": operation.get("current_message"),
                            "payload": metrics,
                        }
                        yield (
                            "event: model_output\ndata: "
                            + json.dumps(payload, ensure_ascii=False)
                            + "\n\n"
                        )
                if run.status in {
                    "completed",
                    "waiting_user",
                    "waiting_author",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    from ..services.novel_creation_run_presentation import present_serialized_run

                    terminal_run = await present_serialized_run(
                        db,
                        run=run,
                        model=run.model_source,
                    )
                    yield (
                        "event: done\ndata: "
                        + json.dumps(terminal_run, ensure_ascii=False)
                        + "\n\n"
                    )
                    return
            finally:
                db.close()
            tick += 1
            if tick % 20 == 0:
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.2)
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
    result = await save_creation_artifact(db, "", {
        "session_id": session_id,
        "stage": stage,
        "data": payload.data if payload.data is not None else confirmation.current_data,
        "confirm": payload.confirm,
        "source": "author",
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
    result = await save_creation_artifact(db, "", {
        "session_id": session_id,
        "stage": stage,
        "data": payload.data,
        "confirm": False,
        "source": "author",
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


class CreationAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    message: str = Field(min_length=1, max_length=1_000_000)
    client_turn_id: str = Field(min_length=36, max_length=36)
    after_sequence: int = Field(default=0, ge=0)
    model: str | None = None
    conversation_id: str | None = Field(default=None, max_length=100)
    assistant_message_id: str | None = Field(default=None, max_length=100)
    model_route: Literal["pc", "mobile"] = "pc"
    mobile_provider: MobileProviderEnvelope | None = Field(default=None, repr=False, exclude=True)
    local_cli_read_permission_grant: Literal["none", "read_once"] = "none"
    local_cli_read_paths: list[str] = Field(default_factory=list, max_length=8)
    reference_context: ReferenceContext | None = None

    @model_validator(mode="after")
    def require_mobile_provider_envelope(self) -> "CreationAgentRequest":
        try:
            uuid.UUID(self.client_turn_id)
        except ValueError as exc:
            raise ValueError("client_turn_id 必须是 UUID") from exc
        if self.assistant_message_id and not self.conversation_id:
            raise ValueError("assistant_message_id requires conversation_id")
        if self.model_route == "mobile" and self.mobile_provider is None:
            raise ValueError("选择手机模型线路时必须提供加密凭据")
        if self.model_route == "pc" and self.mobile_provider is not None:
            raise ValueError("PC 模型线路不能携带手机模型凭据")
        return self


@router.post(
    "/novel-creation/agent-turn",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Reconnectable Creation Agent event stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
    },
)
async def creation_agent_turn(
    payload: CreationAgentRequest,
    request: Request,
    db: Session = Depends(get_db),
    conversations: SystemConversationStore = Depends(get_system_conversation_store),
):
    session = novel_creation_session_store(db).session(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    session_id = str(session.id)
    try:
        creation_agent_conversation(
            conversations,
            session_id=session_id,
            conversation_id=payload.conversation_id,
        )
    except CreationTurnScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    request_provider = _resolve_mobile_creation_provider(
        db,
        payload,
        request,
        binding_id=session_id,
    )
    request_fingerprint = hashlib.sha256(json.dumps({
        "session_id": session_id,
        "message": payload.message,
        "model": payload.model,
        "conversation_id": payload.conversation_id,
        "assistant_message_id": payload.assistant_message_id,
        "model_route": payload.model_route,
        "local_cli_read_permission_grant": payload.local_cli_read_permission_grant,
        "local_cli_read_paths": payload.local_cli_read_paths,
        "reference_context": (
            payload.reference_context.model_dump(mode="json")
            if payload.reference_context is not None
            else None
        ),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    turn_input = CreationAgentTurnInput(
        session_id=session_id,
        message=payload.message,
        client_turn_id=payload.client_turn_id,
        model=payload.model,
        conversation_id=payload.conversation_id,
        assistant_message_id=payload.assistant_message_id,
        local_cli_read_paths=(
            tuple(payload.local_cli_read_paths)
            if payload.local_cli_read_permission_grant == "read_once" else ()
        ),
        reference_context=payload.reference_context,
        request_provider=request_provider,
    )

    async def produce(publish: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        await produce_creation_agent_turn(turn_input, publish)


    return StreamingResponse(
        creation_agent_turn_stream(
            client_turn_id=payload.client_turn_id,
            request_fingerprint=request_fingerprint,
            after_sequence=payload.after_sequence,
            producer=produce,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Preserve the long-standing import surface while route registration lives in
# the bounded auxiliary router module.
AssistantInputRouteRequest = _novel_creation_aux_routes.AssistantInputRouteRequest
CreationImportApplyRequest = _novel_creation_aux_routes.CreationImportApplyRequest
NovelCreationArtifactLockRequest = _novel_creation_aux_routes.NovelCreationArtifactLockRequest
NovelCreationArtifactPatchRequest = _novel_creation_aux_routes.NovelCreationArtifactPatchRequest
NovelCreationArtifactRestoreRequest = _novel_creation_aux_routes.NovelCreationArtifactRestoreRequest
NovelCreationArtifactUndoRequest = _novel_creation_aux_routes.NovelCreationArtifactUndoRequest
NovelCreationEntityDeleteRequest = _novel_creation_aux_routes.NovelCreationEntityDeleteRequest
NovelCreationEntityPatchRequest = _novel_creation_aux_routes.NovelCreationEntityPatchRequest
NovelCreationFinalizeRequest = _novel_creation_aux_routes.NovelCreationFinalizeRequest
NovelCreationSessionPatchRequest = _novel_creation_aux_routes.NovelCreationSessionPatchRequest
NovelCreationStartRequest = _novel_creation_aux_routes.NovelCreationStartRequest
SaveImportedFileRequest = _novel_creation_aux_routes.SaveImportedFileRequest
apply_material_import_endpoint = _novel_creation_aux_routes.apply_material_import_endpoint
create_material_import = _novel_creation_aux_routes.create_material_import
delete_creation_entity_endpoint = _novel_creation_aux_routes.delete_creation_entity_endpoint
delete_creation_session = _novel_creation_aux_routes.delete_creation_session
finalize_creation = _novel_creation_aux_routes.finalize_creation
get_creation_artifact = _novel_creation_aux_routes.get_creation_artifact
get_creation_artifact_version = _novel_creation_aux_routes.get_creation_artifact_version
get_creation_artifact_versions = _novel_creation_aux_routes.get_creation_artifact_versions
get_creation_artifacts = _novel_creation_aux_routes.get_creation_artifacts
get_creation_dependencies = _novel_creation_aux_routes.get_creation_dependencies
get_creation_dependency_graph = _novel_creation_aux_routes.get_creation_dependency_graph
get_creation_entities = _novel_creation_aux_routes.get_creation_entities
get_creation_entity_endpoint = _novel_creation_aux_routes.get_creation_entity_endpoint
get_creation_session = _novel_creation_aux_routes.get_creation_session
get_material_import = _novel_creation_aux_routes.get_material_import
list_creation_sessions = _novel_creation_aux_routes.list_creation_sessions
list_imported_files = _novel_creation_aux_routes.list_imported_files
list_material_imports = _novel_creation_aux_routes.list_material_imports
lock_creation_artifact_fields = _novel_creation_aux_routes.lock_creation_artifact_fields
novel_creation_presets = _novel_creation_aux_routes.novel_creation_presets
patch_creation_artifact_endpoint = _novel_creation_aux_routes.patch_creation_artifact_endpoint
patch_creation_entity_endpoint = _novel_creation_aux_routes.patch_creation_entity_endpoint
read_imported_file = _novel_creation_aux_routes.read_imported_file
restore_creation_artifact_version = _novel_creation_aux_routes.restore_creation_artifact_version
retry_material_import = _novel_creation_aux_routes.retry_material_import
route_assistant_input = _novel_creation_aux_routes.route_assistant_input
route_assistant_input_file = _novel_creation_aux_routes.route_assistant_input_file
save_imported_file = _novel_creation_aux_routes.save_imported_file
start_creation = _novel_creation_aux_routes.start_creation
undo_creation_artifact_endpoint = _novel_creation_aux_routes.undo_creation_artifact_endpoint
unlock_creation_artifact_fields = _novel_creation_aux_routes.unlock_creation_artifact_fields
update_creation_session = _novel_creation_aux_routes.update_creation_session
validate_creation_session_consistency = (
    _novel_creation_aux_routes.validate_creation_session_consistency
)
