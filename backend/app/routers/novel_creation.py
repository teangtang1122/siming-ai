"""REST API for API-free novel creation workflow."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..ai.local_cli_adapter import is_local_cli_provider
from ..core.response import ApiResponse
from ..database.session import get_db
from ..database.models import NovelCreationSession, NovelCreationStageRun
from ..database.session import SessionLocal
from ..services.novel_creation_workspace import (
    STAGE_ORDER,
    create_run,
    generation_blockers,
    get_presets,
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
from ..services.workspace.tools.novel_creation import (
    advance_novel_creation_interview,
    apply_novel_blueprint,
    draft_novel_blueprint,
    review_novel_blueprint,
    start_novel_creation_session,
)
from ..services.workspace.tools.novel_creation_v2 import generate_novel_creation_stage, submit_novel_creation_stage

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
    db.commit()
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


class NovelCreationInterviewNextRequest(BaseModel):
    user_brief: str = ""
    model: str | None = None
    qa_history: list[dict[str, str]] = Field(default_factory=list)
    skip_questions: bool = False


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


@router.post("/novel-creation/sessions/{session_id}/interview/next")
async def advance_creation_interview(
    session_id: str,
    payload: NovelCreationInterviewNextRequest,
    db: Session = Depends(get_db),
):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    operation_id = _start_inline_operation(
        db,
        source_kind="novel_interview",
        title="新书立项 · 动态采访",
        phase="interview",
        model=payload.model,
        resume_url=f"/novel-creation?session={session_id}",
        input_value={"session_id": session_id, **payload.model_dump()},
        input_revision=int(session.revision or 0) if session else None,
    )

    async def run_interview() -> dict[str, Any]:
        result = await advance_novel_creation_interview(
            db,
            "",
            {**payload.model_dump(), "session_id": session_id},
        )
        if result.get("status") != "ok":
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
            raise HTTPException(
                status_code=422,
                detail={
                    "message": result.get("detail") or "动态采访失败",
                    "failure_class": runtime.get("failure_class") or data.get("failure_class"),
                    "next_action": runtime.get("next_action") or data.get("next_action"),
                    "runtime": runtime,
                },
            )
        return result

    result = await _run_inline_operation(
        operation_id,
        run_interview,
        success_message="本轮动态采访已完成",
    )
    return ApiResponse.success(data=result.get("data"), message=result.get("detail") or "采访状态已更新")


@router.post("/novel-creation/review")
async def review_blueprint(payload: NovelCreationReviewRequest, db: Session = Depends(get_db)):
    result = await review_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


@router.post("/novel-creation/apply")
async def apply_blueprint(payload: NovelCreationApplyRequest, db: Session = Depends(get_db)):
    result = await apply_novel_blueprint(db, "", payload.model_dump())
    return _tool_response(result)


class NovelCreationSessionPatchRequest(BaseModel):
    form: dict[str, Any] | None = None
    selected_concept_id: str | None = None
    quick_mode: bool | None = None
    expected_revision: int | None = None


class NovelCreationStageRunRequest(BaseModel):
    stage: str
    model: str | None = None
    use_model: bool = True
    auto_confirm: bool = False
    operation: str = "generate"
    session_patch: dict[str, Any] | None = None
    expected_revision: int | None = None


class NovelCreationStageConfirmRequest(BaseModel):
    data: dict[str, Any] | None = None
    confirm: bool = True
    source: str = "author"
    expected_revision: int | None = None


class NovelCreationStagePatchRequest(BaseModel):
    data: dict[str, Any]
    source: str = "author"
    expected_revision: int


@router.get("/novel-creation/presets")
async def novel_creation_presets():
    return ApiResponse.success(data=get_presets())


@router.get("/novel-creation/sessions")
async def list_creation_sessions(include_completed: bool = False, db: Session = Depends(get_db)):
    query = db.query(NovelCreationSession)
    if not include_completed:
        query = query.filter(NovelCreationSession.status.in_(["drafting", "reviewing", "failed"]))
    sessions = query.order_by(NovelCreationSession.updated_at.desc(), NovelCreationSession.created_at.desc()).limit(30).all()
    return ApiResponse.success(data={"sessions": [serialize_session(item, include_runs=False) for item in sessions]})


@router.get("/novel-creation/sessions/{session_id}")
async def get_creation_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    return ApiResponse.success(data=serialize_session(session))


@router.patch("/novel-creation/sessions/{session_id}")
async def update_creation_session(session_id: str, payload: NovelCreationSessionPatchRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
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
        db.commit()
        return ApiResponse.success(data=serialize_session(session), message="立项草稿已保存")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/novel-creation/sessions/{session_id}")
async def delete_creation_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        return ApiResponse.success(data={"deleted": False})
    if session.created_project_id:
        raise HTTPException(status_code=409, detail="该立项已创建正式作品，不能删除会话记录")
    db.delete(session)
    db.commit()
    return ApiResponse.success(data={"deleted": True})


async def _run_creation_stage(run_id: str, session_id: str, request: dict[str, Any]) -> None:
    db = SessionLocal()
    heartbeat_task: asyncio.Task | None = None
    run: NovelCreationStageRun | None = None
    try:
        run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
        operation_id = run.operation_id if run else None
        if operation_id:
            heartbeat_task = asyncio.create_task(heartbeat_loop(operation_id))
        with activate_operation(operation_id):
            await generate_novel_creation_stage(db, "", {**request, "session_id": session_id, "_run_id": run_id})
    except asyncio.CancelledError:
        run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
        if run and run.status == "running":
            run.status = "cancelled"
            run.current_message = "立项任务已取消，已保存内容不会丢失"
            run.completed_at = datetime.utcnow()
            db.commit()
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if run and run.operation_id:
            unregister_operation_actions(run.operation_id)
        db.close()


@router.post("/novel-creation/sessions/{session_id}/runs")
async def start_creation_stage_run(session_id: str, payload: NovelCreationStageRunRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
    if payload.stage not in {*STAGE_ORDER, "all"}:
        raise HTTPException(status_code=400, detail="未知立项阶段")
    if payload.expected_revision is not None and int(session.revision or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "立项草稿版本已经变化，请确认当前内容后重新生成",
                "current_revision": int(session.revision or 0),
                "session": serialize_session(session),
            },
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
    existing = (
        db.query(NovelCreationStageRun)
        .filter(
            NovelCreationStageRun.session_id == session_id,
            NovelCreationStageRun.stage == payload.stage,
            NovelCreationStageRun.status == "running",
        )
        .order_by(NovelCreationStageRun.created_at.desc())
        .first()
    )
    if existing:
        return ApiResponse.success(
            data={"run": serialize_run(existing), "stream_url": f"/api/novel-creation/runs/{existing.id}/stream"},
            message="该阶段任务仍在运行，已恢复订阅",
        )
    request = payload.model_dump()
    if payload.session_patch:
        patch_session(session, payload.session_patch)
        request["session_patch"] = None
    run = create_run(db, session, payload.stage, request)
    db.commit()
    run_id = run.id
    task = asyncio.create_task(_run_creation_stage(run_id, session_id, request))
    if run.operation_id:
        register_operation_actions(run.operation_id, cancel=task.cancel)
    return ApiResponse.success(data={"run": serialize_run(run), "stream_url": f"/api/novel-creation/runs/{run_id}/stream"}, message="阶段任务已创建")


@router.get("/novel-creation/runs/{run_id}")
async def get_creation_stage_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="阶段任务不存在")
    return ApiResponse.success(data=serialize_run(run))


@router.get("/novel-creation/runs/{run_id}/stream")
async def stream_creation_stage_run(run_id: str):
    async def events():
        sent = 0
        tick = 0
        while True:
            db = SessionLocal()
            try:
                run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == run_id).first()
                if not run:
                    yield "event: error\ndata: " + json.dumps({"message": "阶段任务不存在"}, ensure_ascii=False) + "\n\n"
                    return
                rows = list(run.events or [])
                for event in rows[sent:]:
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "status": event.status,
                        "message": event.message,
                        "payload": event.payload_json,
                    }
                    yield f"event: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                sent = len(rows)
                if run.status in {"completed", "failed", "cancelled"}:
                    yield "event: done\ndata: " + json.dumps(serialize_run(run), ensure_ascii=False) + "\n\n"
                    return
            finally:
                db.close()
            tick += 1
            if tick % 20 == 0:
                yield "event: heartbeat\ndata: {}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/novel-creation/sessions/{session_id}/stages/{stage}/confirm")
async def confirm_creation_stage(session_id: str, stage: str, payload: NovelCreationStageConfirmRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="立项草稿不存在")
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
        "data": payload.data,
        "confirm": payload.confirm,
        "source": payload.source,
        "expected_revision": payload.expected_revision,
    })
    if payload.confirm:
        from ..database.models import OperationRun
        from ..services.operation_runtime import update_operation

        latest_run = (
            db.query(NovelCreationStageRun)
            .filter(
                NovelCreationStageRun.session_id == session_id,
                NovelCreationStageRun.stage.in_([stage, "all"]),
                NovelCreationStageRun.operation_id.isnot(None),
            )
            .order_by(NovelCreationStageRun.completed_at.desc(), NovelCreationStageRun.created_at.desc())
            .first()
        )
        if latest_run and latest_run.operation_id:
            operation = db.query(OperationRun).filter(OperationRun.id == latest_run.operation_id).first()
            if operation and operation.status == "waiting_user":
                update_operation(
                    db,
                    operation,
                    status="completed",
                    message="阶段内容已由作者确认",
                    next_action="继续处理下一阶段",
                    attention={},
                    result={
                        "summary": "阶段内容已生成并由作者确认",
                        "completed": ["阶段生成", "作者确认"],
                        "incomplete": [],
                    },
                    outcome="completed_with_tools",
                    event_type="confirmed",
                    checkpoint=True,
                )
                db.commit()
    return _tool_response(result)


@router.patch("/novel-creation/sessions/{session_id}/stages/{stage}")
async def update_creation_stage(session_id: str, stage: str, payload: NovelCreationStagePatchRequest, db: Session = Depends(get_db)):
    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()
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


class RefreshQuestionRequest(BaseModel):
    session_id: str
    question: str
    existing_options: list[str] = []
    user_brief: str = ""
    model: str | None = None


@router.post("/novel-creation/refresh-question")
async def refresh_question(payload: RefreshQuestionRequest, db: Session = Depends(get_db)):
    from app.services.workspace.tools.novel_creation import refresh_question_options

    session = db.query(NovelCreationSession).filter(NovelCreationSession.id == payload.session_id).first()
    operation_id = _start_inline_operation(
        db,
        source_kind="novel_interview_option",
        title="新书立项 · 更换回答选项",
        phase="refreshing_option",
        model=payload.model,
        resume_url=f"/novel-creation?session={payload.session_id}",
        input_value=payload.model_dump(),
        input_revision=int(session.revision or 0) if session else None,
    )

    async def run_refresh() -> dict[str, Any]:
        return await refresh_question_options(
            db=db,
            session_id=payload.session_id,
            question=payload.question,
            existing_options=payload.existing_options,
            user_brief=payload.user_brief,
            model=payload.model,
        )

    try:
        result = await _run_inline_operation(operation_id, run_refresh, success_message="新的回答选项已生成")
    except HTTPException:
        raise
    except Exception as exc:
        raise _inline_operation_http_error(exc) from exc
    return ApiResponse.success(data=result)


class SystemChatRequest(BaseModel):
    message: str
    model: str | None = None
    context: dict[str, Any] | None = None  # {blueprints, sessionId, brief, importedFiles, history}


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


class SaveImportedFileRequest(BaseModel):
    filename: str
    content: str


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
