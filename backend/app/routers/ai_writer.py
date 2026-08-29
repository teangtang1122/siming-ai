"""AI Writing Engine — narrator, character dialogue, dialogue battle, text ops, conflict, changes."""
from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
    tool_category_controller_schema,
    tool_names_for_categories,
)
from app.architecture.uow import commit_session
import json
import asyncio
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError, LLMError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)
from ..modules.assistant.interfaces.workspace_dependencies import assistant_workspace
from ..services.content_store import ensure_project_folder
from ..prompts.workspace_assistant import (
    build_workspace_assistant_initial_user_message,
    redact_tool_result_for_model,
    _compress_search_result,
    MAX_ITERATIONS,
)
from ..services.agent.prompt_builder import build_system_prompt, get_workspace_pack
from ..ai.local_cli_adapter import is_local_cli_provider
from ..ai.local_cli_prompt import supports_direct_mcp
from ..services.style_rules import (
    _detect_forbidden_sentence_violations,
    _mechanical_repair_forbidden_sentences,  # noqa: F401 - compatibility export
    _repair_forbidden_sentence_text,
)
from ..services.workspace.tool_schemas import (
    build_workspace_tool_schemas,
    select_workspace_tool_names,
)
from ..services.workspace.registry import registry
from ..services.workspace import execute_workspace_action
from ..services.tool_category_state import (
    activate_tool_categories,
    create_tool_category_state,
    read_tool_category_state,
    remove_tool_category_state,
)
from ..services.workspace.run_log import (
    create_assistant_run,
    finish_run_step,
    mark_assistant_run,
    resolve_assistant_model,
    run_payload,
    start_run_step,
    step_payload,
)
from ..services.workspace.assistant_stream_runtime import assistant_cancel_was_explicit, detached_assistant_stream
from ..services.workspace.run_recovery import (
    generate_idempotency_key,
    retry_step,
    resume_from_step,
    resume_run,
)
from ..services.workspace.assistant_response import (
    WorkspaceTurnTelemetry,
    _assistant_conversation_to_dict,
    _assistant_message_to_dict,
    _workspace_outcome,
    finalize_workspace_assistant_turn,
)
from ..services.workspace.generated_drafts import pending_chapter_draft_ids
from ..services.workspace.outline_drafts import pending_outline_draft_ids
from ..services.workspace.terminal_draft_detection import local_cli_terminal_draft
from ..services.workspace.turn_control import (
    AssistantTurnDirective,
    apply_turn_directive,
    is_terminal_tool_result,
    terminal_reply as terminal_tool_reply,
)
from ..schemas.ai_writer import WorkspaceAssistantRequest, WorkspaceAssistantRunDetailResponse, WorkspaceAssistantRunListResponse

router = APIRouter(tags=["ai-writer"])

_ASSISTANT_STREAM_TIMEOUT_SECONDS = 300
_WORKSPACE_ASSISTANT_SCOPE = "project"

def _assistant_history_text(history: list[dict], limit: int = 8) -> str:
    lines = []
    for item in (history or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        # Truncate assistant messages aggressively — full planning text confuses the model
        # into thinking the instructions in history are still active tasks
        max_len = 4000 if item.get("role") == "user" else 600
        lines.append(f"{role}：{content[:max_len]}")
    return "\n\n".join(lines) or "暂无对话历史。"


def _workspace_category_result(
    arguments: dict[str, Any],
    authorized_tool_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    try:
        categories = normalize_tool_categories(arguments.get("enabled_categories"))
    except ValueError as exc:
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "error",
            "detail": str(exc),
            "data": None,
        }, None
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    available = authorized_tool_names & set(tool_names_for_categories(categories))
    detail = f"已准备{'、'.join(labels)}能力，共 {len(available)} 项可用工具" if labels else "已关闭全部业务工具"
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": {
            "enabled_categories": list(categories),
            "labels": labels,
            "available_tool_count": len(available),
        },
    }, categories


def _workspace_category_instruction(
    categories: tuple[str, ...],
    *,
    category_selected: bool,
) -> str:
    if not category_selected:
        return (
            "当前只开放 set_tool_categories，必须先调用它选择完成用户最新消息所需的类别；"
            "在控制工具返回前不得直接回答、等待或声称工具不可用。调用后立即结束当前模型步骤。"
        )
    if not categories:
        return (
            "本轮已经通过 set_tool_categories 明确关闭全部业务工具。"
            "现在可以直接完成不需要业务工具的回复；如需业务能力，重新调用 set_tool_categories，"
            "调用后立即结束当前模型步骤。"
        )
    labels = "、".join(TOOL_CATEGORY_METADATA[category]["label"] for category in categories)
    return (
        f"当前开放工具类别：{labels}。直接完成用户最新任务；需要更换能力时调用 set_tool_categories，"
        "调用后立即结束当前模型步骤。"
    )


def _get_assistant_conversation_or_404(
    db: Session,
    project_id: str,
    conversation_id: str,
) -> Any:
    conversation = assistant_workspace(db).conversation(project_id, conversation_id)
    if not conversation:
        raise NotFoundError("助手对话不存在")
    return conversation


def _assistant_history_from_messages(
    db: Session,
    conversation_id: str,
    before_message_id: Optional[str] = None,
    limit: int = 8,
) -> str:
    messages = assistant_workspace(db).conversation_messages(conversation_id)
    history: list[dict] = []
    for message in messages:
        if before_message_id and message.id == before_message_id:
            break
        if message.status not in {"completed", "running"}:
            continue
        history.append({"role": message.role, "content": message.content})
    return _assistant_history_text(history, limit=limit)


def _assistant_title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


async def _execute_workspace_action(
    db: Session,
    project_id: str,
    action: dict,
    model: Optional[str] = None,
    authorized_tool_names: set[str] | None = None,
) -> dict:
    """Execute a workspace tool action, with pre-flight forbidden-pattern check for chapter creation."""
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
        args = {**args, "model": model}
        action = {**action, "arguments": args}

    return await execute_workspace_action(db, project_id, action)


# ---------------------------------------------------------------------------
# SSE streaming helper
# ---------------------------------------------------------------------------

async def _sse_writer_stream(
    generator: AsyncGenerator[str, None],
    project: Optional[Any] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    full_text = ""
    try:
        async for chunk in generator:
            full_text += chunk
            data = json.dumps({"type": "token", "content": chunk}, ensure_ascii=False, separators=(",", ":"))
            yield f"data: {data}\n\n"
        if project:
            violations = _detect_forbidden_sentence_violations(full_text, project)
            if violations:
                yield _sse_event({
                    "type": "style_check",
                    "status": "running",
                    "message": f"发现 {len(violations)} 处禁用句式，正在自动修订",
                    "violations": violations[:8],
                })
                try:
                    repaired, before, remaining = await _repair_forbidden_sentence_text(
                        full_text,
                        project,
                        model,
                        max_tokens,
                    )
                    full_text = repaired
                    yield _sse_event({
                        "type": "style_repaired",
                        "status": "ok" if not remaining else "warning",
                        "message": "禁用句式已自动修订" if not remaining else f"仍有 {len(remaining)} 处需要人工确认",
                        "full_text": full_text,
                        "violations": before[:8],
                        "remaining": remaining[:8],
                    })
                except Exception as exc:
                    yield _sse_event({
                        "type": "style_repaired",
                        "status": "error",
                        "message": f"禁用句式自动修订失败：{exc}",
                        "full_text": full_text,
                        "violations": violations[:8],
                    })
        done_data = json.dumps(
            {"type": "done", "full_text": full_text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        yield f"data: {done_data}\n\n"
        yield "data: [DONE]\n\n"
    except LLMError as e:
        error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False, separators=(",", ":"))
        yield f"data: {error_data}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        error_data = json.dumps(
            {"type": "error", "message": f"服务器错误: {e}"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        yield f"data: {error_data}\n\n"
        yield "data: [DONE]\n\n"


def _sse_event(payload) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


# ---------------------------------------------------------------------------
# Narrator generation (SSE)
# Autonomous story assistant
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/ai/assistant/conversations")
async def list_assistant_conversations(project_id: str, db: Session = Depends(get_db)):
    """List persisted assistant conversations for a project."""
    get_project_or_404(db, project_id)
    conversations = assistant_workspace(db).conversations_with_counts(project_id, _WORKSPACE_ASSISTANT_SCOPE)
    items = []
    for conversation, message_count in conversations:
        items.append(_assistant_conversation_to_dict(conversation, message_count))
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
    return ApiResponse.success(data={
        "conversation": _assistant_conversation_to_dict(conversation, len(messages)),
        "messages": [_assistant_message_to_dict(message) for message in messages],
    })


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


def _maybe_json(text: Optional[str]):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


@router.get("/projects/{project_id}/ai/assistant/runs", response_model=ApiResponse[WorkspaceAssistantRunListResponse])
async def list_assistant_runs(
    project_id: str,
    conversation_id: Optional[str] = None,
    limit: int = 30,
    db: Session = Depends(get_db),
):
    """List durable workspace-assistant execution runs."""
    get_project_or_404(db, project_id)
    limit = max(1, min(limit, 100))
    runs = assistant_workspace(db).runs(
        project_id,
        conversation_id,
        limit=limit,
    )
    return ApiResponse.success(data={
        "items": [run_payload(run) for run in runs],
        "total": len(runs),
    })


@router.get("/projects/{project_id}/ai/assistant/runs/{run_id}", response_model=ApiResponse[WorkspaceAssistantRunDetailResponse])
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
    assistant_message = workspace.message(run.assistant_message_id) if run.assistant_message_id else None
    return ApiResponse.success(data={
        "run": run_payload(run),
        "assistant_message": _assistant_message_to_dict(assistant_message) if assistant_message else None,
        "steps": [
            {
                **step_payload(step),
                "request": _maybe_json(step.request_json),
                "result": _maybe_json(step.result_json),
            }
            for step in steps
        ],
    })


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/steps/{step_id}/retry")
async def retry_assistant_run_step(
    project_id: str,
    run_id: str,
    step_id: str,
    db: Session = Depends(get_db),
):
    """Retry a failed workspace assistant run step (preserves original)."""
    get_project_or_404(db, project_id)
    run = assistant_workspace(db).run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    try:
        result = await retry_step(db, run.id, step_id)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return ApiResponse.success(data=result)


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/steps/{step_id}/resume-from")
async def resume_from_assistant_run_step(
    project_id: str,
    run_id: str,
    step_id: str,
    db: Session = Depends(get_db),
):
    """Retry a step and continue with downstream failed steps."""
    get_project_or_404(db, project_id)
    run = assistant_workspace(db).run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    try:
        results = await resume_from_step(db, run.id, step_id)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return ApiResponse.success(data=results)


@router.post("/projects/{project_id}/ai/assistant/runs/{run_id}/resume")
async def resume_assistant_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Retry all unresolved error steps in a run."""
    get_project_or_404(db, project_id)
    run = assistant_workspace(db).run(project_id, run_id)
    if not run:
        raise NotFoundError("助手任务不存在")
    try:
        results = await resume_run(db, run.id)
    except ValueError as exc:
        raise ValidationError(str(exc))
    return ApiResponse.success(data=results)


# ---------------------------------------------------------------------------
# Memory management API
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/ai/assistant/memories")
async def list_assistant_memories(
    project_id: str,
    category: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List saved memories for a project."""
    get_project_or_404(db, project_id)
    from ..services.workspace.tools.memory import list_memories
    result = await list_memories(db, project_id, {"category": category or "", "limit": limit})
    return ApiResponse.success(data=result.get("data", []))


@router.delete("/projects/{project_id}/ai/assistant/memories/{memory_id}")
async def delete_assistant_memory(
    project_id: str,
    memory_id: str,
    db: Session = Depends(get_db),
):
    """Delete a single memory by ID."""
    get_project_or_404(db, project_id)
    from ..services.workspace.tools.memory import forget
    result = await forget(db, project_id, {"id": memory_id})
    if result.get("status") == "error":
        raise NotFoundError(result.get("detail", "记忆不存在"))
    return ApiResponse.success(message=result.get("detail", "已删除"))


# ---------------------------------------------------------------------------
# Agentic workspace assistant helpers
# ---------------------------------------------------------------------------


def _trim_context_if_needed(messages: list[dict], max_chars: int = 800_000) -> list[dict]:
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total <= max_chars:
        return messages
    kept = messages[:2]
    recent = messages[-6:] if len(messages) > 6 else messages[2:]
    return kept + recent


@router.post("/projects/{project_id}/ai/workspace-assistant/stream")
async def workspace_assistant_stream(
    project_id: str,
    payload: WorkspaceAssistantRequest,
    request: Request,
    db: Session = Depends(get_db),
    system_conversations: SystemConversationStore = Depends(get_system_conversation_store),
):
    """Conversational assistant with multi-turn agentic loop — search → reason → act."""
    get_project_or_404(db, project_id)
    request_provider = None
    if payload.model_route == "mobile":
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
            project_id=project_id,
        )
        payload.mobile_provider = None
        payload.model = f"{request_provider.provider}:{request_provider.default_model}"
    if payload.canonical_conversation_id:
        try:
            canonical = system_conversations.get(payload.canonical_conversation_id)["conversation"]
        except NotFoundError as exc:
            raise ValidationError(
                "Canonical project conversation does not belong to this project"
            ) from exc
        if canonical.get("scope_type") != "project" or canonical.get("scope_id") != project_id:
            raise ValidationError("Canonical project conversation does not belong to this project")
    # Pin the selected default at submission time. This makes the initial SSE
    # event/query expose the real model and prevents a global-model change from
    # switching a task between iterations.
    payload.model = resolve_assistant_model(payload.model)

    # The selected model receives the latest request, real project context and
    # its authorized tools below. Siming does not pre-classify natural-language
    # intent or choose a business target before the Agent acts.
    try:
        selected_provider = LLMGateway.provider_for_model(payload.model)
    except Exception:
        selected_provider = ""
    async def event_generator(run_db: Session):
        db = run_db
        if payload.canonical_conversation_id and not payload.conversation_id:
            workspace = assistant_workspace(db)
            execution_conversation = workspace.conversation_by_canonical(
                project_id,
                payload.canonical_conversation_id,
            )
            if not execution_conversation:
                execution_conversation = workspace.create_conversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(payload.message),
                    scope=_WORKSPACE_ASSISTANT_SCOPE,
                    canonical_conversation_id=payload.canonical_conversation_id,
                )
                commit_session(db)
            payload.conversation_id = execution_conversation.id
        # API models use native function calls. Local Agent CLIs use their
        # configured Siming MCP catalog and choose tools themselves.
        conversation = None
        user_msg_db = None
        assistant_msg_db = None
        assistant_run = None
        tool_logs: list[dict] = []
        # Declared at function scope so GeneratorExit recovery can access them
        final_reply = ""
        applied_actions: list[dict] = []
        searched_context: list[dict] = []
        final_model = ""
        final_usage = None
        turn_telemetry = WorkspaceTurnTelemetry()
        tool_category_state_file = ""
        try:
            # --- Phase 1: Setup ---
            if payload.conversation_id:
                conversation = _get_assistant_conversation_or_404(db, project_id, payload.conversation_id)
                conversation.scope = _WORKSPACE_ASSISTANT_SCOPE
            else:
                conversation = assistant_workspace(db).create_conversation(
                    project_id=project_id,
                    title=_assistant_title_from_message(payload.message),
                    scope=_WORKSPACE_ASSISTANT_SCOPE,
                )
                db.flush()
            conversation.model = payload.model
            conversation.updated_at = datetime.utcnow()

            created_at = datetime.utcnow()
            workspace = assistant_workspace(db)
            user_msg_db = workspace.create_message(
                conversation_id=conversation.id,
                role="user",
                content=payload.message,
                status="completed",
                created_at=created_at,
                updated_at=created_at,
            )
            assistant_msg_db = workspace.create_message(
                conversation_id=conversation.id,
                role="assistant",
                content="正在分析需求...",
                status="running",
                payload_json=json.dumps({"tool_logs": []}, ensure_ascii=False),
                created_at=created_at + timedelta(microseconds=1),
                updated_at=created_at + timedelta(microseconds=1),
            )
            commit_session(db)
            db.refresh(conversation)
            db.refresh(user_msg_db)
            db.refresh(assistant_msg_db)
            assistant_run = create_assistant_run(
                db,
                project_id=project_id,
                conversation_id=conversation.id,
                user_message_id=user_msg_db.id,
                assistant_message_id=assistant_msg_db.id,
                scope=_WORKSPACE_ASSISTANT_SCOPE,
                model=payload.model,
            )

            yield _sse_event({
                "type": "conversation",
                "conversation": _assistant_conversation_to_dict(conversation),
                "user_message": _assistant_message_to_dict(user_msg_db),
                "assistant_message": _assistant_message_to_dict(assistant_msg_db),
            })
            yield _sse_event({"type": "run", "run": run_payload(assistant_run)})

            # --- Phase 2: Build minimal initial messages ---
            project = get_project_or_404(db, project_id)
            project_folder = str(ensure_project_folder(db, project))
            commit_session(db)
            pending_draft_ids_before = pending_chapter_draft_ids(db, project_id)
            pending_outline_draft_ids_before = pending_outline_draft_ids(db, project_id)
            local_cli_extra_body = LLMGateway.local_cli_extra_body(
                payload.model,
                cwd=project_folder,
            )
            local_cli_selected = is_local_cli_provider(selected_provider)
            local_cli_mcp_enabled = (
                local_cli_selected and supports_direct_mcp(selected_provider)
            )
            if local_cli_mcp_enabled:
                tool_category_state_file = create_tool_category_state()
            if local_cli_selected:
                local_cli_read_permission_granted = (
                    selected_provider == "opencode_cli"
                    and payload.local_cli_read_permission_grant == "read_once"
                    and bool(payload.local_cli_read_paths)
                )
                local_cli_extra_body = dict(local_cli_extra_body)
                local_cli_extra_body.update(
                    {
                        "local_cli_mcp_authorized": local_cli_mcp_enabled,
                        "local_cli_allow_mcp": local_cli_mcp_enabled,
                        "local_cli_read_permission_granted": local_cli_read_permission_granted,
                        "local_cli_read_paths": (
                            list(payload.local_cli_read_paths)
                            if local_cli_read_permission_granted else []
                        ),
                        "local_cli_isolated": True,
                        "local_cli_mcp_permission_pack": "project_management",
                        "local_cli_mcp_project_id": project_id,
                        "local_cli_mcp_tool_category_state_file": tool_category_state_file,
                        "local_cli_terminal_draft_project_id": (
                            project_id if local_cli_mcp_enabled else ""
                        ),
                        "local_cli_terminal_draft_excluded_ids": sorted(pending_draft_ids_before),
                        "local_cli_terminal_outline_draft_excluded_ids": sorted(
                            pending_outline_draft_ids_before
                        ),
                    }
                )
            if assistant_run.operation_id:
                local_cli_extra_body = dict(local_cli_extra_body or {})
                local_cli_extra_body["operation_id"] = assistant_run.operation_id
            explicit_context: list[str] = []
            if payload.selected_text and payload.selected_text.strip():
                chapter_label = ""
                if payload.selected_text_chapter_id:
                    chapter = assistant_workspace(db).chapter(
                        project_id,
                        payload.selected_text_chapter_id,
                    )
                    if chapter:
                        chapter_label = f"，来自章节「{chapter.title}」"
                explicit_context.append(f"用户明确选中了以下文本{chapter_label}：\n```\n{payload.selected_text.strip()}\n```")

            history_text = _assistant_history_from_messages(db, conversation.id, before_message_id=user_msg_db.id, limit=8)
            if history_text == "暂无对话历史。":
                history_text = _assistant_history_text(payload.history)

            authorized_tool_names = set(select_workspace_tool_names())
            if local_cli_mcp_enabled:
                authorized_tool_names = {
                    tool.name for tool in registry.list_for_mcp(permission_pack="project_management")
                }
            active_categories: tuple[str, ...] = ()
            category_selected = False
            observed_category_version = 0
            workspace_tool_names: list[str] = []
            workspace_tool_name_set: set[str] = {TOOL_CATEGORY_CONTROLLER}
            workspace_tool_schemas = [tool_category_controller_schema()]

            base_system_prompt = build_system_prompt(
                get_workspace_pack(),
                outline_batch_count=payload.outline_batch_count,
            )

            if local_cli_mcp_enabled:
                local_cli_contract = (
                    "当前进程已连接仅限本轮、仅限当前作品的 Siming MCP 服务器 siming_turn。"
                    "项目数据的读取和修改必须直接调用该服务器中的工具；"
                    "不要输出工具 JSON，不要启动另一个 CLI，不要修改任何全局 MCP 配置。"
                    "请依据用户最新消息和真实项目数据自行判断任务、选择目标与工具。"
                    "若决定生成章节正文，必须先取得真实章级大纲 ID，再读取写作上下文并保存一份未入库草稿；"
                    "草稿保存成功后立即结束，不得继续执行角色、关系、世界观或建档写入。"
                )
                base_system_prompt = f"{base_system_prompt}\n\n{local_cli_contract}"
            initial_user = build_workspace_assistant_initial_user_message(
                project_id=project_id,
                project_title=project.title,
                history_text=history_text,
                explicit_context=explicit_context,
                outline_batch_count=payload.outline_batch_count,
                user_message=payload.message,
            )
            messages: list[dict] = [
                {
                    "role": "system",
                    "content": (
                        f"{base_system_prompt}\n\n"
                        f"{_workspace_category_instruction(active_categories, category_selected=category_selected)}"
                    ),
                },
                {"role": "user", "content": initial_user},
            ]

            # --- Phase 3: Agentic loop ---
            yield _sse_event({"type": "status", "message": "AI 助手开始分析和检索资料...", "tool": "agent_loop"})

            searched_queries: set[tuple] = set()
            turn_terminal_result: dict[str, Any] | None = None
            try:
                supports_function_calling = LLMGateway.supports_tool_calling(payload.model)
            except Exception:
                supports_function_calling = True
            if not supports_function_calling and not local_cli_mcp_enabled:
                raise LLMError(
                    "当前模型不支持原生工具调用，也没有可用的进程级 Siming MCP；"
                    "无法执行项目 Agent 任务"
                )
            use_function_calling = supports_function_calling

            if not supports_function_calling:
                yield _sse_event({
                    "type": "status",
                    "message": (
                        "本机 CLI 已连接当前作品范围的临时 Siming MCP，"
                        "可自行选择项目读写工具。"
                    ),
                    "tool": "local_cli_mcp_mode",
                })

            for iteration in range(1, MAX_ITERATIONS + 1):
                scoped_names = authorized_tool_names & set(tool_names_for_categories(active_categories))
                workspace_tool_names = sorted(scoped_names)
                workspace_tool_name_set = {TOOL_CATEGORY_CONTROLLER, *workspace_tool_names}
                workspace_tool_schemas = [
                    tool_category_controller_schema(),
                    *build_workspace_tool_schemas(workspace_tool_names),
                ]
                messages[0] = {
                    "role": "system",
                    "content": (
                        f"{base_system_prompt}\n\n"
                        f"{_workspace_category_instruction(active_categories, category_selected=category_selected)}"
                    ),
                }
                yield _sse_event({
                    "type": "iteration_start",
                    "iteration": iteration,
                    "message": f"第 {iteration}/{MAX_ITERATIONS} 轮推理",
                })

                messages = _trim_context_if_needed(messages)

                if use_function_calling:
                    # --- Function calling path ---
                    content_buffer: list[str] = []
                    tool_call_buffers: dict[int, dict] = {}
                    fc_error = None
                    reasoning_buffer = ""
                    provider_state: list[dict] = []
                    resume_notices: list[dict[str, Any]] = []

                    def capture_stream_resume(info: dict[str, Any]) -> None:
                        resume_notices.append(dict(info))

                    try:
                        stream_gen = LLMGateway.stream_chat_completion_with_tools(
                            messages=messages,
                            model=payload.model,
                            temperature=payload.temperature or 0.3,
                            max_tokens=payload.max_tokens,
                            timeout=_ASSISTANT_STREAM_TIMEOUT_SECONDS,
                            retry=1,
                            resume=8,
                            on_resume=capture_stream_resume,
                            extra_body=local_cli_extra_body,
                            tools=workspace_tool_schemas,
                            tool_choice="required" if not category_selected else "auto",
                        )
                        async for chunk in stream_gen:
                            while resume_notices:
                                notice = resume_notices.pop(0)
                                checkpoint_chars = max(0, int(notice.get("checkpoint_chars") or 0))
                                resume_message = (
                                    "模型连接中断，正在从已验证的文字检查点继续…"
                                    if checkpoint_chars else "模型工具响应中断，正在重新获取完整工具调用…"
                                )
                                turn_telemetry.report_model_activity(assistant_run, resume_message, message=resume_message)
                                yield _sse_event({
                                    "type": "status",
                                    "message": resume_message,
                                    "tool": "stream_resume",
                                })
                            if chunk["type"] == "content_delta":
                                content_buffer.append(chunk["delta"])
                                turn_telemetry.report_model_activity(assistant_run, chunk["delta"])
                                yield _sse_event({"type": "content_delta", "delta": chunk["delta"]})
                            elif chunk["type"] == "reasoning_delta":
                                reasoning_buffer += chunk["delta"]
                                turn_telemetry.report_model_activity(assistant_run, chunk["delta"], message="模型正在思考")
                                visible_delta = turn_telemetry.record_reasoning_delta(chunk["delta"], iteration)
                                if visible_delta:
                                    yield _sse_event({
                                        "type": "reasoning_delta",
                                        "delta": visible_delta,
                                        "iteration": iteration,
                                    })
                            elif chunk["type"] == "tool_call_delta":
                                turn_telemetry.report_model_activity(
                                    assistant_run,
                                    chunk.get("name") or chunk.get("arguments_delta") or "",
                                    signal="tool",
                                    message="模型正在准备工具调用",
                                )
                                idx = chunk["index"]
                                if idx not in tool_call_buffers:
                                    tool_call_buffers[idx] = {"id": chunk.get("id", ""), "name": "", "arguments": ""}
                                buf = tool_call_buffers[idx]
                                if chunk.get("id"):
                                    buf["id"] = chunk["id"]
                                if chunk.get("name"):
                                    buf["name"] = chunk["name"]
                                    yield _sse_event({
                                        "type": "tool_call",
                                        "tool": chunk["name"],
                                        "args": {},
                                    })
                                if chunk.get("arguments_delta"):
                                    buf["arguments"] += chunk["arguments_delta"]
                            elif chunk["type"] == "done":
                                if not reasoning_buffer:
                                    reasoning_buffer = chunk.get("reasoning_content", "")
                                    visible_delta = turn_telemetry.record_reasoning_delta(
                                        reasoning_buffer,
                                        iteration,
                                    )
                                    if visible_delta:
                                        yield _sse_event({
                                            "type": "reasoning_delta",
                                            "delta": visible_delta,
                                            "iteration": iteration,
                                        })
                                provider_state = chunk.get("provider_state") or []
                    except LLMError as e:
                        fc_error = e
                        if "API Key" in str(e) or "提供商" in str(e):
                            raise
                    except Exception as e:
                        fc_error = e

                    if fc_error is not None:
                        err_msg = str(fc_error)
                        err_type = type(fc_error).__name__
                        yield _sse_event({
                            "type": "status",
                            "message": f"原生工具调用失败（{err_type}: {err_msg}），本轮已停止。",
                            "tool": "native_tool_protocol_error",
                        })
                        raise fc_error

                if not use_function_calling:
                    raw_buffer: list[str] = []
                    stream_error: Exception | None = None
                    resume_notices: list[dict[str, Any]] = []

                    def capture_text_stream_resume(info: dict[str, Any]) -> None:
                        resume_notices.append(dict(info))

                    stream_gen = LLMGateway.stream_chat_completion(
                        messages=messages,
                        model=payload.model,
                        temperature=payload.temperature or 0.3,
                        max_tokens=payload.max_tokens,
                        timeout=_ASSISTANT_STREAM_TIMEOUT_SECONDS,
                        # A direct-MCP CLI can commit writes before its buffered
                        # final text is returned. Re-running that process after
                        # a transport stop would cross the tool idempotency
                        # boundary, so only pure model streams auto-resume.
                        retry=0 if local_cli_mcp_enabled else 1,
                        resume=0 if local_cli_mcp_enabled else 8,
                        on_resume=capture_text_stream_resume,
                        extra_body=local_cli_extra_body,
                    )
                    try:
                        async for chunk in stream_gen:
                            while resume_notices:
                                resume_notices.pop(0)
                                resume_message = "模型连接中断，正在从已验证的文字检查点继续…"
                                turn_telemetry.report_model_activity(assistant_run, resume_message, message=resume_message)
                                yield _sse_event({
                                    "type": "status",
                                    "message": resume_message,
                                    "tool": "stream_resume",
                                })
                            raw_buffer.append(chunk)
                            turn_telemetry.report_model_activity(assistant_run, chunk)
                            yield _sse_event({"type": "content_delta", "delta": chunk})
                    except Exception as stream_err:
                        stream_error = stream_err
                        yield _sse_event({"type": "status", "message": f"流式输出中断，尝试用已接收内容继续：{stream_err}", "tool": "stream_error"})
                    raw_content = "".join(raw_buffer)
                    if local_cli_mcp_enabled and tool_category_state_file:
                        category_state = read_tool_category_state(tool_category_state_file)
                        next_version = int(category_state.get("version") or 0)
                        if next_version > observed_category_version:
                            observed_category_version = next_version
                            requested = normalize_tool_categories(
                                category_state.get("requested_categories") or [],
                            )
                            category_result, selected_categories = _workspace_category_result(
                                {"enabled_categories": list(requested)},
                                authorized_tool_names,
                            )
                            if selected_categories is not None:
                                active_categories = selected_categories
                                category_selected = True
                                activate_tool_categories(tool_category_state_file)
                            tool_logs.append({
                                "tool": TOOL_CATEGORY_CONTROLLER,
                                "status": category_result.get("status") or "ok",
                                "detail": category_result.get("detail") or "",
                            })
                            yield _sse_event({
                                "type": "tool_categories_changed",
                                "tool": TOOL_CATEGORY_CONTROLLER,
                                "result": category_result,
                                "iteration": iteration,
                            })
                            yield _sse_event({
                                "type": "iteration_end",
                                "iteration": iteration,
                                "message": "工具类别已切换，正在按新类别启动下一模型步骤",
                            })
                            continue
                    if local_cli_mcp_enabled and not category_selected and stream_error is None:
                        raise LLMError(
                            "本机 CLI 没有调用临时 MCP 中唯一开放的 set_tool_categories，"
                            "本轮已终止，未接受 CLI 返回的等待或完成文字"
                        )
                    if local_cli_mcp_enabled:
                        # The CLI can commit the terminal draft before its
                        # buffered final text reaches this process. Durable
                        # draft evidence is authoritative even when transport
                        # completion was lost.
                        db.expire_all()
                        detected_draft = local_cli_terminal_draft(
                            db,
                            project_id,
                            pending_draft_ids_before,
                            pending_outline_draft_ids_before,
                        )
                        if detected_draft is not None:
                            turn_terminal_result, terminal_message = detected_draft
                            draft_tool = str(turn_terminal_result["tool"])
                            applied_actions.append(turn_terminal_result)
                            tool_logs.append({
                                "tool": draft_tool,
                                "status": "ok",
                                "detail": turn_terminal_result["detail"],
                            })
                            final_reply = terminal_tool_reply(turn_terminal_result)
                            final_model = payload.model or ""
                            final_usage = None
                            yield _sse_event({
                                "type": "write_result",
                                "tool": draft_tool,
                                "result": turn_terminal_result,
                                "iteration": iteration,
                            })
                            yield _sse_event({
                                "type": "iteration_end",
                                "iteration": iteration,
                                "message": terminal_message,
                            })
                            break
                    if stream_error is not None:
                        detail = str(stream_error)
                        tool_logs.append({"tool": "stream_error", "status": "error", "detail": detail})
                        final_reply = (
                            "本机 CLI 连接中断。为避免重复执行可能已经提交的 MCP 写入，"
                            "系统没有自动重启该进程；已提交结果以当前项目数据为准，"
                            "下次请求会从真实项目状态继续。"
                            if local_cli_mcp_enabled else f"模型调用中断，未执行写入：{detail}"
                        )
                        final_model = payload.model or ""
                        final_usage = None
                        yield _sse_event({
                            "type": "iteration_end",
                            "iteration": iteration,
                            "message": "模型输出中断，本轮已停止执行",
                        })
                        break
                    final_reply = raw_content.strip()
                    final_model = payload.model or ""
                    final_usage = None
                    completion_message = (
                        "本机 CLI 已通过原生 MCP 完成本轮并返回文字结果"
                        if local_cli_mcp_enabled
                        else "模型执行器已完成本轮并返回文字结果"
                    )
                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": completion_message,
                    })
                    break

                # --- Function calling: process accumulated tool calls ---
                reply_text = "".join(content_buffer)

                # Build tool_calls list from accumulated buffers
                tool_calls: list[dict] = []
                for idx in sorted(tool_call_buffers.keys()):
                    buf = tool_call_buffers[idx]
                    if not buf["name"]:
                        continue
                    try:
                        args = json.loads(buf["arguments"]) if buf["arguments"].strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append({
                        "id": buf["id"],
                        "type": "function",
                        "function": {
                            "name": buf["name"],
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    })

                category_calls = [
                    call for call in tool_calls
                    if call["function"]["name"] == TOOL_CATEGORY_CONTROLLER
                ]
                if category_calls:
                    # A category replacement invalidates every other schema in
                    # this model step, regardless of emitted call order.
                    tool_calls = category_calls[:1]
                else:
                    if not category_selected and tool_calls:
                        raise LLMError(
                            "模型没有调用本步骤唯一开放的 set_tool_categories，"
                            "本轮已终止，未执行模型虚构的其他工具调用"
                        )
                    # A generated author-review draft is the sole business operation in its batch.
                    draft_calls = [
                        call
                        for call in tool_calls
                        if call["function"]["name"]
                        in {"chapter_writer", "outline_writer", "save_external_chapter_draft"}
                    ]
                    if draft_calls:
                        tool_calls = draft_calls[:1]

                wr_names = {
                    name for name in workspace_tool_name_set
                    if (definition := registry.get(name)) is not None
                    and definition.tool_type == "write"
                }
                se_names = workspace_tool_name_set - wr_names - {TOOL_CATEGORY_CONTROLLER}
                yield _sse_event({
                    "type": "tool",
                    "tool": "tool_batch",
                    "status": "ok",
                    "detail": f"第 {iteration} 轮：{len(tool_calls)} 个工具调用（{len([t for t in tool_calls if t['function']['name'] in se_names])} 个搜索，{len([t for t in tool_calls if t['function']['name'] in wr_names])} 个写入）",
                })

                # Agent decides it's done — no tool calls, just text
                if not tool_calls:
                    if not category_selected:
                        raise LLMError(
                            "模型没有调用本步骤唯一开放的 set_tool_categories，"
                            "本轮已终止，未接受模型伪造的等待或完成回复"
                        )
                    if not reply_text.strip():
                        final_reply = "当前模型没有返回文字或工具调用，本轮未执行任何操作。"
                    else:
                        final_reply = reply_text
                    final_model = payload.model or ""
                    final_usage = None
                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": "Agent 判断任务完成",
                    })
                    break

                # Agent called tools — execute ALL of them (search and write alike)
                all_results: list[dict] = []
                native_stop_reason = ""
                category_changed = False
                for tc in tool_calls[:12]:
                    tool_name = tc["function"]["name"]
                    try:
                        tc_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        tc_args = {}

                    dedup_key = (tool_name, json.dumps(tc_args, ensure_ascii=False, sort_keys=True))
                    is_write = tool_name in wr_names
                    action_type = (
                        "control" if tool_name == TOOL_CATEGORY_CONTROLLER
                        else "write" if is_write
                        else "search"
                    )

                    if native_stop_reason:
                        break
                    if dedup_key in searched_queries:
                        native_stop_reason = "duplicate_tool"
                        break
                    searched_queries.add(dedup_key)

                    action = {"tool": tool_name, "arguments": tc_args}
                    _idem_key = generate_idempotency_key(db, tool_name, project_id, tc_args) if is_write else None
                    step = start_run_step(
                        db,
                        assistant_run,
                        step_type=action_type,
                        tool=tool_name,
                        iteration=iteration,
                        request=tc_args,
                        idempotency_key=_idem_key,
                    )
                    yield _sse_event({
                        "type": f"{action_type}_start",
                        "tool": tool_name,
                        "args": tc_args,
                        "iteration": iteration,
                        "step_id": step.id if step else None,
                    })
                    if tool_name == TOOL_CATEGORY_CONTROLLER:
                        action_result, selected_categories = _workspace_category_result(
                            tc_args,
                            authorized_tool_names,
                        )
                        if selected_categories is not None:
                            active_categories = selected_categories
                            category_selected = True
                        category_changed = True
                    else:
                        try:
                            action_result = await _execute_workspace_action(
                                db,
                                project_id,
                                action,
                                model=payload.model,
                                authorized_tool_names=workspace_tool_name_set,
                            )
                        except Exception as exc:
                            action_result = {"tool": tool_name, "status": "error", "detail": str(exc), "data": []}
                    finish_run_step(
                        db,
                        step,
                        status=str(action_result.get("status") or "ok"),
                        result=action_result,
                        detail=str(action_result.get("detail") or ""),
                        error=str(action_result.get("detail") or "") if action_result.get("status") == "error" else None,
                    )

                    all_results.append(action_result)
                    tool_logs.append({
                        "tool": action_result.get("tool") or tool_name,
                        "status": action_result.get("status") or "ok",
                        "detail": action_result.get("detail") or "",
                    })
                    yield _sse_event({
                        "type": f"{action_type}_result",
                        "tool": tool_name,
                        "result": action_result,
                        "iteration": iteration,
                        "step_id": step.id if step else None,
                    })
                    if category_changed:
                        break
                    if "cataloging" in tool_name:
                        turn_terminal_result = action_result
                        native_stop_reason = "cataloging_status"
                        break
                    elif is_terminal_tool_result(action_result):
                        turn_terminal_result = action_result
                        native_stop_reason = "terminal_tool"
                        applied_actions.append(action_result)
                        break

                for action_result in all_results:
                    if action_result.get("tool") in se_names:
                        compressed = _compress_search_result(action_result)
                        if compressed:
                            searched_context.append(compressed)

                if category_changed:
                    assistant_tool_calls = [
                        {"id": tc["id"], "type": "function", "function": tc["function"]}
                        for tc in tool_calls
                    ]
                    category_message: dict[str, Any] = {
                        "role": "assistant",
                        "content": reply_text or None,
                        "tool_calls": assistant_tool_calls,
                    }
                    if reasoning_buffer:
                        category_message["reasoning_content"] = reasoning_buffer
                    if provider_state:
                        category_message["provider_state"] = provider_state
                    messages.append(category_message)
                    for tc, action_result in zip(tool_calls, all_results):
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(action_result, ensure_ascii=False),
                        })
                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": "工具类别已切换，当前模型步骤结束",
                    })
                    continue

                if native_stop_reason:
                    if turn_terminal_result and is_terminal_tool_result(turn_terminal_result):
                        final_reply = terminal_tool_reply(turn_terminal_result)
                    elif turn_terminal_result:
                        final_reply = str(turn_terminal_result.get("detail") or "已查询建档状态，本轮结束。")
                    else:
                        final_reply = "本轮没有获得新的查询结果，已停止重复推理。"
                    yield _sse_event({
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": "已到达服务端回合终止边界，不再调用模型",
                    })
                    break

                # Feed results back as tool_result messages
                assistant_tool_calls = [
                    {"id": tc["id"], "type": "function", "function": tc["function"]}
                    for tc in tool_calls
                ]
                _asst_msg = {
                    "role": "assistant",
                    "content": reply_text or None,
                    "tool_calls": assistant_tool_calls,
                }
                if reasoning_buffer:
                    _asst_msg["reasoning_content"] = reasoning_buffer
                if provider_state:
                    _asst_msg["provider_state"] = provider_state
                messages.append(_asst_msg)
                for tc, ar in zip(tool_calls, all_results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(redact_tool_result_for_model(ar), ensure_ascii=False),
                    })

                yield _sse_event({
                    "type": "iteration_end",
                    "iteration": iteration,
                    "message": f"第 {iteration} 轮完成，执行了 {len(tool_calls)} 个工具",
                })
                # No continue here — loop naturally goes to next iteration
            else:
                # Loop completed without break (shouldn't happen, but guard)
                final_reply = "已分析完毕。"

            # --- Phase 4: Finalize ---
            response_payload = finalize_workspace_assistant_turn(
                db,
                assistant_run=assistant_run,
                assistant_message=assistant_msg_db,
                conversation=conversation,
                final_reply=final_reply,
                applied_actions=applied_actions,
                tool_logs=tool_logs,
                searched_context=searched_context,
                final_model=final_model,
                final_usage=final_usage,
                reasoning_content="".join(turn_telemetry.reasoning_parts).strip(),
            )
            yield _sse_event({"type": "complete", "data": response_payload})
            yield _sse_event("[DONE]")
        except (GeneratorExit, asyncio.CancelledError):
            # Only an explicit Operation cancellation closes this producer.
            # Never execute deferred writes while unwinding a cancelled stream.
            if not assistant_cancel_was_explicit():
                raise
            if assistant_msg_db:
                assistant_msg_db.content = "任务已取消，本轮不会再写入章节。"
                assistant_msg_db.status = "aborted"
                assistant_msg_db.updated_at = datetime.utcnow()
                if conversation:
                    conversation.updated_at = datetime.utcnow()
                commit_session(db)
            mark_assistant_run(
                db,
                assistant_run,
                status="cancelled",
                phase="cancelled",
                error="用户取消了任务",
                final_reply="任务已取消，本轮不会再写入章节。",
            )
            raise
        except LLMError as exc:
            if assistant_msg_db:
                assistant_msg_db.content = str(exc)
                assistant_msg_db.status = "error"
                assistant_msg_db.payload_json = json.dumps({
                    "tool_logs": tool_logs,
                    "reasoning_content": "".join(turn_telemetry.reasoning_parts).strip(),
                }, ensure_ascii=False)
                commit_session(db)
            mark_assistant_run(db, assistant_run, status="error", phase="llm_error", error=str(exc))
            yield _sse_event({"type": "error", "message": str(exc)})
            yield _sse_event("[DONE]")
        except Exception as exc:
            if assistant_msg_db:
                assistant_msg_db.content = f"服务器错误: {exc}"
                assistant_msg_db.status = "error"
                assistant_msg_db.payload_json = json.dumps({
                    "tool_logs": tool_logs,
                    "reasoning_content": "".join(turn_telemetry.reasoning_parts).strip(),
                }, ensure_ascii=False)
                commit_session(db)
            mark_assistant_run(db, assistant_run, status="error", phase="server_error", error=str(exc))
            yield _sse_event({"type": "error", "message": f"服务器错误: {exc}"})
            yield _sse_event("[DONE]")
        finally:
            if tool_category_state_file:
                remove_tool_category_state(tool_category_state_file)

    stream_factory = event_generator

    if request_provider is not None:
        provider_stream_factory = stream_factory

        async def mobile_provider_event_generator(source_db: Session):
            from ..modules.model_runtime.application.request_override import use_request_provider

            with use_request_provider(request_provider):
                async for event in provider_stream_factory(source_db):
                    yield event

        stream_factory = mobile_provider_event_generator

    return StreamingResponse(
        detached_assistant_stream(stream_factory),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
