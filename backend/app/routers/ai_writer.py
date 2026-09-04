# ruff: noqa: B008
"""HTTP and SSE boundaries for AI writing and the workspace assistant."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..architecture.uow import commit_session
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import LLMError, NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)
from ..modules.assistant.interfaces.workspace_dependencies import assistant_workspace
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..schemas.ai_writer import (
    WorkspaceAssistantRequest,
    WorkspaceAssistantRunDetailResponse,
    WorkspaceAssistantRunListResponse,
)
from ..services.conversation_context import prepare_conversation_context
from ..services.style_rules import (
    _detect_forbidden_sentence_violations,
    _mechanical_repair_forbidden_sentences,  # noqa: F401 - compatibility export
    _repair_forbidden_sentence_text,
)
from ..services.workspace import execute_workspace_action
from ..services.workspace.assistant_public_errors import (
    public_model_failure,
    public_server_failure,
)
from ..services.workspace.assistant_response import (
    _assistant_conversation_to_dict,
    _assistant_message_to_dict,
    _workspace_outcome,  # noqa: F401 - compatibility export
)
from ..services.workspace.assistant_stream_runtime import detached_assistant_stream
from ..services.workspace.assistant_turn_runner import WorkspaceAssistantTurnRunner
from ..services.workspace.registry import registry
from ..services.workspace.run_log import resolve_assistant_model, run_payload, step_payload
from ..services.workspace.run_recovery import resume_from_step, resume_run, retry_step

router = APIRouter(tags=["ai-writer"])

_WORKSPACE_ASSISTANT_SCOPE = "project"
logger = logging.getLogger(__name__)


def _get_assistant_conversation_or_404(
    db: Session,
    project_id: str,
    conversation_id: str,
) -> Any:
    conversation = assistant_workspace(db).conversation(project_id, conversation_id)
    if not conversation:
        raise NotFoundError("助手对话不存在")
    return conversation


async def _execute_workspace_action(
    db: Session,
    project_id: str,
    action: dict[str, Any],
    model: str | None = None,
    authorized_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Execute one authorized workspace action."""

    tool = str(action.get("tool") or "").strip()
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    if authorized_tool_names is not None and tool not in authorized_tool_names:
        return {
            "tool": tool or "unknown",
            "status": "blocked",
            "detail": "该工具不在本轮服务端授权的能力集合中，未执行。",
            "data": {},
        }
    tool_def = registry.get(tool)
    accepts_model = (
        bool(tool_def and "model" in tool_def.input_schema)
        or bool(tool_def and "internal_llm" in tool_def.permission_tags)
        or bool(tool_def and tool_def.tool_type == "generator")
    )
    if model and accepts_model and not args.get("model"):
        action = {**action, "arguments": {**args, "model": model}}
    return await execute_workspace_action(db, project_id, action)


async def _sse_writer_stream(
    generator: AsyncGenerator[str, None],
    project: Any | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """Stream plain writer output and apply the deterministic style check."""

    full_text = ""
    try:
        async for chunk in generator:
            full_text += chunk
            yield _sse_event({"type": "token", "content": chunk})
        if project:
            violations = _detect_forbidden_sentence_violations(full_text, project)
            if violations:
                yield _sse_event(
                    {
                        "type": "style_check",
                        "status": "running",
                        "message": f"发现 {len(violations)} 处禁用句式，正在自动修订",
                        "violations": violations[:8],
                    }
                )
                try:
                    repaired, before, remaining = await _repair_forbidden_sentence_text(
                        full_text, project, model, max_tokens
                    )
                    full_text = repaired
                    yield _sse_event(
                        {
                            "type": "style_repaired",
                            "status": "ok" if not remaining else "warning",
                            "message": "禁用句式已自动修订"
                            if not remaining
                            else f"仍有 {len(remaining)} 处需要人工确认",
                            "full_text": full_text,
                            "violations": before[:8],
                            "remaining": remaining[:8],
                        }
                    )
                except Exception:
                    error_id = uuid.uuid4().hex
                    logger.exception("Writer style repair failed error_id=%s", error_id)
                    yield _sse_event(
                        {
                            "type": "style_repaired",
                            "status": "error",
                            "message": f"禁用句式自动修订失败；错误编号：{error_id}",
                            "full_text": full_text,
                            "violations": violations[:8],
                        }
                    )
        yield _sse_event({"type": "done", "full_text": full_text})
        yield _sse_event("[DONE]")
    except LLMError as exc:
        failure = public_model_failure(exc)
        logger.exception("Writer model request failed class=%s", failure.failure_class)
        yield _sse_event({"type": "error", **failure.to_dict()})
        yield _sse_event("[DONE]")
    except Exception:
        error_id = uuid.uuid4().hex
        failure = public_server_failure(error_id)
        logger.exception("Writer request failed error_id=%s", error_id)
        yield _sse_event({"type": "error", **failure.to_dict()})
        yield _sse_event("[DONE]")


def _sse_event(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


@router.get("/projects/{project_id}/ai/assistant/conversations")
async def list_assistant_conversations(project_id: str, db: Session = Depends(get_db)):
    """List persisted assistant conversations for a project."""

    get_project_or_404(db, project_id)
    records = assistant_workspace(db).conversations_with_counts(
        project_id, _WORKSPACE_ASSISTANT_SCOPE
    )
    items = [
        _assistant_conversation_to_dict(conversation, message_count)
        for conversation, message_count in records
    ]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.get("/projects/{project_id}/ai/assistant/conversations/{conversation_id}")
async def get_assistant_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Get one persisted assistant conversation and all messages."""

    conversation = _get_assistant_conversation_or_404(db, project_id, conversation_id)
    messages = assistant_workspace(db).conversation_messages(conversation.id)
    return ApiResponse.success(
        data={
            "conversation": _assistant_conversation_to_dict(conversation, len(messages)),
            "messages": [_assistant_message_to_dict(message) for message in messages],
        }
    )


@router.delete("/projects/{project_id}/ai/assistant/conversations/{conversation_id}")
async def delete_assistant_conversation(
    project_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Delete an assistant conversation."""

    conversation = _get_assistant_conversation_or_404(db, project_id, conversation_id)
    assistant_workspace(db).delete(conversation)
    commit_session(db)
    return ApiResponse.success(message="助手对话已删除")


@router.get(
    "/projects/{project_id}/ai/assistant/runs",
    response_model=ApiResponse[WorkspaceAssistantRunListResponse],
)
async def list_assistant_runs(
    project_id: str,
    conversation_id: str | None = None,
    limit: int = 30,
    db: Session = Depends(get_db),
):
    """List durable workspace-assistant execution runs."""

    get_project_or_404(db, project_id)
    limit = max(1, min(limit, 100))
    runs = assistant_workspace(db).runs(project_id, conversation_id, limit=limit)
    return ApiResponse.success(
        data={
            "items": [run_payload(run) for run in runs],
            "total": len(runs),
        }
    )


@router.get(
    "/projects/{project_id}/ai/assistant/runs/{run_id}",
    response_model=ApiResponse[WorkspaceAssistantRunDetailResponse],
)
async def get_assistant_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Get one workspace-assistant execution run with all step records."""

    workspace = assistant_workspace(db)
    run = workspace.run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    steps = workspace.run_steps(run.id)
    assistant_message = (
        workspace.message(run.assistant_message_id) if run.assistant_message_id else None
    )
    return ApiResponse.success(
        data={
            "run": run_payload(run),
            "assistant_message": _assistant_message_to_dict(assistant_message)
            if assistant_message
            else None,
            "steps": [step_payload(step) for step in steps],
        }
    )


def _assistant_run_or_404(db: Session, project_id: str, run_id: str) -> Any:
    get_project_or_404(db, project_id)
    run = assistant_workspace(db).run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    return run


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/steps/{step_id}/retry")
async def retry_assistant_run_step(
    project_id: str,
    run_id: str,
    step_id: str,
    db: Session = Depends(get_db),
):
    """Retry a failed workspace assistant run step."""

    run = _assistant_run_or_404(db, project_id, run_id)
    try:
        result = await retry_step(db, run.id, step_id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=result)


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/steps/{step_id}/resume-from")
async def resume_from_assistant_run_step(
    project_id: str,
    run_id: str,
    step_id: str,
    db: Session = Depends(get_db),
):
    """Retry a step and continue with downstream failed steps."""

    run = _assistant_run_or_404(db, project_id, run_id)
    try:
        results = await resume_from_step(db, run.id, step_id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=results)


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/resume")
async def resume_assistant_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Retry all unresolved error steps in a run."""

    run = _assistant_run_or_404(db, project_id, run_id)
    try:
        results = await resume_run(db, run.id)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=results)


@router.get("/projects/{project_id}/ai/assistant/memories")
async def list_assistant_memories(
    project_id: str,
    category: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List saved memories for a project."""

    from ..services.workspace.tools.memory import list_memories

    get_project_or_404(db, project_id)
    result = await list_memories(db, project_id, {"category": category or "", "limit": limit})
    return ApiResponse.success(data=result.get("data", []))


@router.delete("/projects/{project_id}/ai/assistant/memories/{memory_id}")
async def delete_assistant_memory(
    project_id: str,
    memory_id: str,
    db: Session = Depends(get_db),
):
    """Delete a single memory by ID."""

    from ..services.workspace.tools.memory import forget

    get_project_or_404(db, project_id)
    result = await forget(db, project_id, {"id": memory_id})
    if result.get("status") == "error":
        raise NotFoundError(result.get("detail", "记忆不存在"))
    return ApiResponse.success(message=result.get("detail", "已删除"))


@router.post("/projects/{project_id}/ai/workspace-assistant/stream")
async def workspace_assistant_stream(
    project_id: str,
    payload: WorkspaceAssistantRequest,
    request: Request,
    db: Session = Depends(get_db),
    system_conversations: SystemConversationStore = Depends(get_system_conversation_store),
):
    """Validate the request and delegate one detached assistant turn."""

    get_project_or_404(db, project_id)
    request_provider = _mobile_request_provider(db, project_id, payload, request)
    _validate_canonical_conversation(project_id, payload, system_conversations)
    payload.model = resolve_assistant_model(payload.model)
    try:
        selected_provider = LLMGateway.provider_for_model(payload.model)
    except Exception:
        selected_provider = ""
    runner = WorkspaceAssistantTurnRunner(
        project_id=project_id,
        payload=payload,
        selected_provider=selected_provider,
        gateway=LLMGateway,
        registry=registry,
        workspace_factory=assistant_workspace,
        execute_action=_execute_workspace_action,
        prepare_context=prepare_conversation_context,
        encode_event=_sse_event,
    )
    stream_factory = runner.events
    if request_provider is not None:
        stream_factory = _provider_scoped_stream(runner.events, request_provider)
    return StreamingResponse(
        detached_assistant_stream(stream_factory),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _mobile_request_provider(
    db: Session,
    project_id: str,
    payload: WorkspaceAssistantRequest,
    request: Request,
) -> Any:
    if payload.model_route != "mobile":
        return None
    if getattr(request.state, "gateway_device_platform", None) != "android" or not getattr(
        request.state, "gateway_device_id", None
    ):
        raise ValidationError("手机模型线路只允许已配对的 Android 设备使用")
    from ..services.mobile_provider_envelope import decrypt_mobile_provider

    provider = decrypt_mobile_provider(
        db,
        payload.mobile_provider,
        device_id=request.state.gateway_device_id,
        project_id=project_id,
    )
    payload.mobile_provider = None
    payload.model = f"{provider.provider}:{provider.default_model}"
    return provider


def _validate_canonical_conversation(
    project_id: str,
    payload: WorkspaceAssistantRequest,
    store: SystemConversationStore,
) -> None:
    if not payload.canonical_conversation_id:
        return
    try:
        canonical = store.get(payload.canonical_conversation_id)["conversation"]
    except NotFoundError as exc:
        raise ValidationError(
            "Canonical project conversation does not belong to this project"
        ) from exc
    if canonical.get("scope_type") != "project" or canonical.get("scope_id") != project_id:
        raise ValidationError("Canonical project conversation does not belong to this project")


def _provider_scoped_stream(source: Any, request_provider: Any) -> Any:
    async def provider_events(db: Session) -> AsyncGenerator[str, None]:
        from ..modules.model_runtime.application.request_override import use_request_provider

        with use_request_provider(request_provider):
            async for event in source(db):
                yield event

    return provider_events
