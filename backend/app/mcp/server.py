"""MCP server protocol handler.

Processes JSON-RPC messages for the MCP protocol. This module handles
the message framing and dispatches to adapter/permissions layers.

V1 serves over stdio only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import suppress
from typing import Any, TextIO

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_category_controller_schema,
)
from app.architecture.uow import commit_session
from app.core.legacy_env import get_compatible_env
from app.mcp.adapter import (
    McpResultAuditError,
    execute_tool,
    list_mcp_tools,
    project_tool_result,
    tool_result_payload,
)
from app.mcp.prompts import list_prompts, render_prompt
from app.mcp.schemas import McpToolResult, make_text_result
from app.modules.creation.interfaces.agent_progress import (
    creation_tool_completed_event,
    creation_tool_started_event,
)
from app.modules.creation.interfaces.agent_scope import CREATION_AGENT_WRITE_TOOL_NAMES
from app.services.tool_category_state import (
    append_tool_category_audit,
    append_tool_category_event,
    creation_turn_write_denial_for_state,
    creation_turn_write_tools_closed,
    read_tool_category_state,
    record_creation_turn_write_result,
    replace_tool_categories,
)
from app.services.workspace.direct_mcp_run_log import (
    WorkspaceDirectMcpRunLogError,
    WorkspaceDirectMcpStepStart,
    begin_workspace_direct_mcp_step,
    cas_workspace_direct_mcp_lease,
    claim_workspace_direct_mcp_step,
    resolve_workspace_direct_mcp_lease,
)
from app.services.workspace.registry import registry
from app.services.workspace.run_log import finish_run_step
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

# ── MCP protocol constants ───────────────────────────────────────────────

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "siming"
SERVER_VERSION = APP_VERSION

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TOOL_NOT_FOUND = -32000
PERMISSION_DENIED = -32001
PROJECT_NOT_FOUND = -32002
TOOL_EXECUTION_FAILED = -32003


def _jsonrpc_error(id: Any, code: int, message: str, data: Any = None) -> str:
    """Build a JSON-RPC error response string."""
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    resp = {"jsonrpc": "2.0", "id": id, "error": err}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _jsonrpc_result(id: Any, result: Any) -> str:
    """Build a JSON-RPC success response string."""
    resp = {"jsonrpc": "2.0", "id": id, "result": result}
    # Keep the wire payload ASCII-safe for Windows stdio MCP clients. JSON
    # parsers still recover the original Unicode strings after decoding.
    return json.dumps(resp, ensure_ascii=True)


def _configure_stdio_utf8() -> None:
    """Prefer UTF-8 stdio when the host process supports reconfiguration."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


def handle_message(
    raw: str,
    *,
    db: Any = None,
    project_id: str = "",
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
    direct_mcp_lease_token: str = "",
) -> str:
    """Process one JSON-RPC message and return the response string.

    Args:
        raw: The raw JSON-RPC message string.
        db: SQLAlchemy session (required for tools/call).
        project_id: Current project ID (required for tools/call).
        allowed_tiers: Permission tiers to allow. Defaults to {"readonly"}.
        permission_pack: Permission pack name. If set, overrides allowed_tiers.
        creation_session_id: Session boundary required by the creation_session pack.

    Returns:
        JSON-RPC response string.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return _jsonrpc_error(None, PARSE_ERROR, "Invalid JSON")

    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})
    managed_workspace = (
        permission_pack == "project_management" and bool(tool_category_state_file)
    )

    if method == "initialize":
        return _handle_initialize(msg_id, params, expose_prompts=not managed_workspace)
    elif method == "tools/list":
        return _handle_tools_list(
            msg_id,
            allowed_tiers,
            permission_pack,
            tool_category_state_file,
        )
    elif method == "tools/call":
        return _handle_tools_call(
            msg_id,
            params,
            db,
            project_id,
            allowed_tiers,
            permission_pack,
            creation_session_id,
            tool_category_state_file,
            direct_mcp_lease_token,
        )
    elif method == "prompts/list":
        if permission_pack == "creation_session" or managed_workspace:
            return _jsonrpc_result(msg_id, {"prompts": []})
        return _handle_prompts_list(msg_id)
    elif method == "prompts/get":
        if permission_pack == "creation_session" or managed_workspace:
            return _jsonrpc_error(
                msg_id,
                PERMISSION_DENIED,
                "Prompts are not exposed in this managed Agent turn",
            )
        return _handle_prompts_get(msg_id, params, db)
    elif method == "ping":
        return _jsonrpc_result(msg_id, {})
    else:
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _handle_initialize(
    msg_id: Any,
    params: dict,
    *,
    expose_prompts: bool = True,
) -> str:
    """Handle the MCP initialize handshake."""
    result = {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }
    if expose_prompts:
        result["capabilities"]["prompts"] = {"listChanged": False}
    return _jsonrpc_result(msg_id, result)


def _handle_tools_list(
    msg_id: Any,
    allowed_tiers: set[str],
    permission_pack: str | None = None,
    tool_category_state_file: str = "",
) -> str:
    """Handle tools/list request."""
    tools = list_mcp_tools(allowed_tiers=allowed_tiers, permission_pack=permission_pack)
    if permission_pack == "project_management" and tool_category_state_file:
        allowed_names = {
            definition.name for definition in registry.list_for_workspace_direct_mcp()
        }
        tools = [tool for tool in tools if tool.name in allowed_names]
    if tool_category_state_file:
        try:
            state = read_tool_category_state(tool_category_state_file)
        except ValueError as exc:
            return _jsonrpc_error(msg_id, PERMISSION_DENIED, str(exc))
        enabled = set(state.get("active_categories") or [])
        tools = [
            tool for tool in tools
            if (definition := registry.get(tool.name)) is not None
            and definition.agent_category in enabled
        ]
        if permission_pack == "creation_session" and creation_turn_write_tools_closed(state):
            tools = [tool for tool in tools if tool.name not in CREATION_AGENT_WRITE_TOOL_NAMES]
    tool_dicts = []
    if tool_category_state_file:
        controller = tool_category_controller_schema()["function"]
        tool_dicts.append({
            "name": controller["name"],
            "description": controller["description"],
            "inputSchema": controller["parameters"],
            # The controller mutates only the isolated per-turn capability
            # state.  Keep it reviewable, but declare the remaining MCP safety
            # hints so clients do not infer destructive or open-world access.
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        })
    for t in tools:
        definition = registry.get(t.name)
        read_only = bool(
            definition is not None
            and not definition.writes_project_data
            and definition.tool_type in {"read", "analysis", "web"}
        )
        tool_dicts.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": {
                "readOnlyHint": read_only,
                "destructiveHint": bool(
                    definition is not None
                    and not read_only
                    and definition.requires_confirmation
                ),
                "idempotentHint": bool(
                    read_only or (definition is not None and definition.idempotent)
                ),
                "openWorldHint": bool(
                    definition is not None and definition.tool_type == "web"
                ),
            },
        })
    return _jsonrpc_result(msg_id, {"tools": tool_dicts})


def _handle_prompts_list(msg_id: Any) -> str:
    """Handle prompts/list request."""
    prompts = []
    for prompt in list_prompts():
        prompts.append({
            "name": prompt.name,
            "description": prompt.description,
            "arguments": [
                {
                    "name": arg.name,
                    "description": arg.description,
                    "required": arg.required,
                }
                for arg in prompt.args
            ],
        })
    return _jsonrpc_result(msg_id, {"prompts": prompts})


def _handle_prompts_get(msg_id: Any, params: dict, db: Any) -> str:
    """Handle prompts/get request."""
    if db is None:
        return _jsonrpc_error(
            msg_id,
            INTERNAL_ERROR,
            "Database session not available for prompt rendering",
        )
    name = str(params.get("name") or "").strip()
    arguments = params.get("arguments", {})
    if not name:
        return _jsonrpc_error(msg_id, INVALID_PARAMS, "Prompt name is required")
    if not isinstance(arguments, dict):
        arguments = {}
    messages = render_prompt(db, name, {str(k): str(v) for k, v in arguments.items()})
    if messages is None:
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Prompt not found: {name}")
    # Governed prompt rendering can prepare a persisted baseline manifest.
    # Commit it before handing the ID to an MCP client so a later evidence or
    # formal-write call can validate the exact same sources.
    commit_session(db)
    return _jsonrpc_result(msg_id, {
        "description": f"Siming prompt: {name}",
        "messages": [
            {
                "role": message.role,
                "content": {"type": "text", "text": message.content},
            }
            for message in messages
        ],
    })


def _category_scoped_call_result(
    tool_name: str,
    arguments: dict[str, Any],
    tool_category_state_file: str,
) -> McpToolResult | None:
    """Handle the category controller and reject tools outside the active set."""

    try:
        state = read_tool_category_state(tool_category_state_file)
    except ValueError as exc:
        return make_text_result(
            json.dumps({"status": "denied", "detail": str(exc)}, ensure_ascii=False),
            is_error=True,
        )
    if tool_name == TOOL_CATEGORY_CONTROLLER:
        try:
            payload = replace_tool_categories(
                tool_category_state_file,
                arguments.get("enabled_categories"),
            )
        except ValueError as exc:
            payload = {
                "tool": tool_name,
                "status": "error",
                "detail": str(exc),
                "data": None,
            }
        append_tool_category_audit(tool_category_state_file, {
            "tool": tool_name,
            "arguments": arguments,
            "status": payload.get("status"),
            "result": payload,
        })
        return make_text_result(
            json.dumps(payload, ensure_ascii=False),
            is_error=payload.get("status") != "ok",
        )
    definition = registry.get(tool_name)
    enabled = set(state.get("active_categories") or [])
    category_change_pending = int(state.get("active_version") or 0) < int(state.get("version") or 0)
    if category_change_pending or definition is None or definition.agent_category not in enabled:
        payload = {
            "tool": tool_name,
            "status": "denied",
            "detail": (
                "工具类别已经切换，当前模型步骤已结束"
                if category_change_pending
                else "该工具所属类别当前未开放"
            ),
        }
        append_tool_category_audit(tool_category_state_file, {
            "tool": tool_name,
            "arguments": arguments,
            "status": "denied",
            "result": payload,
        })
        return make_text_result(
            json.dumps(payload, ensure_ascii=False),
            is_error=True,
        )
    return None


def _creation_turn_write_scoped_call_result(
    tool_name: str,
    arguments: dict[str, Any],
    tool_category_state_file: str,
) -> McpToolResult | None:
    """Enforce the one-user-message mutation boundary before execution."""

    if tool_name not in CREATION_AGENT_WRITE_TOOL_NAMES:
        return None
    try:
        payload = creation_turn_write_denial_for_state(
            tool_category_state_file,
            tool_name,
        )
    except ValueError as exc:
        payload = {
            "tool": tool_name,
            "status": "denied",
            "detail": str(exc),
            "data": {"reason": "invalid_turn_state"},
        }
    if payload is None:
        return None
    append_tool_category_audit(tool_category_state_file, {
        "tool": tool_name,
        "arguments": arguments,
        "status": "denied",
        "result": payload,
    })
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    append_tool_category_event(tool_category_state_file, {
        "type": "tool_completed",
        "message": str(payload.get("detail") or "本轮写工具已经关闭"),
        "data": {
            "tool": tool_name,
            "status": "denied",
            "turn_boundary": str(data.get("reason") or "write_limit"),
        },
    })
    return make_text_result(json.dumps(payload, ensure_ascii=False), is_error=True)


def _turn_guard_scoped_call_result(
    db: Any,
    *,
    project_id: str,
    permission_pack: str | None,
    tool_category_state_file: str,
    direct_mcp_lease_token: str,
) -> McpToolResult | None:
    """Deny every managed-turn MCP call once a newer user supersedes it."""

    if permission_pack not in {"project_management", "creation_session"}:
        return None
    try:
        state = read_tool_category_state(tool_category_state_file)
    except ValueError as exc:
        return make_text_result(
            json.dumps({"status": "denied", "detail": str(exc)}, ensure_ascii=False),
            is_error=True,
        )
    guard = state.get("turn_guard")
    if not isinstance(guard, dict):
        # Category state is also used by standalone MCP callers that are not
        # attached to a durable Agent run.  Only an explicitly bound guard
        # opts a surface into supersession checks.
        return None
    if db is None:
        active = False
    else:
        # MCP tool execution commits or rolls back every preceding business
        # call.  End the remaining read transaction so this check observes a
        # superseding desktop process before dispatching the next handler.
        db.rollback()
        kind = str(guard.get("kind") or "")
        if kind == "workspace":
            try:
                resolve_workspace_direct_mcp_lease(
                    db,
                    project_id=project_id,
                    lease_token=direct_mcp_lease_token,
                )
            except WorkspaceDirectMcpRunLogError:
                active = False
            else:
                active = True
        elif kind == "creation":
            from app.modules.assistant.infrastructure.models import (
                SystemAssistantConversation,
                SystemAssistantMessage,
            )

            active = (
                db.query(SystemAssistantMessage.id)
                .join(
                    SystemAssistantConversation,
                    SystemAssistantConversation.id
                    == SystemAssistantMessage.conversation_id,
                )
                .filter(
                    SystemAssistantMessage.id
                    == str(guard.get("assistant_message_id") or ""),
                    SystemAssistantMessage.conversation_id
                    == str(guard.get("conversation_id") or ""),
                    SystemAssistantMessage.status == "running",
                    SystemAssistantConversation.scope_type == "creation",
                    SystemAssistantConversation.scope_id
                    == str(guard.get("session_id") or ""),
                )
                .first()
                is not None
            )
        else:
            active = False
    if active:
        return None
    payload = {
        "status": "denied",
        "detail": "本轮已被更新的作者消息替换，未执行业务工具。",
        "data": {"reason": "turn_superseded"},
    }
    append_tool_category_audit(
        tool_category_state_file,
        {"tool": "turn_guard", "status": "denied", "result": payload},
    )
    return make_text_result(json.dumps(payload, ensure_ascii=False), is_error=True)


def _record_scoped_tool_result(
    *,
    tool_category_state_file: str,
    permission_pack: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    result_payload: dict[str, Any],
    run_step: WorkspaceDirectMcpStepStart | None = None,
    replayed: bool = False,
) -> None:
    """Audit one scoped result and advance the shared creation write budget."""

    audit_record: dict[str, Any] = {
        "tool": tool_name,
        "arguments": arguments,
        "status": result_payload.get("status"),
        "result": result_payload,
    }
    if run_step is not None:
        audit_record["assistant_run_step_id"] = str(run_step.step.id)
        audit_record["result_ref"] = f"assistant_run_step:{run_step.step.id}"
        audit_record["replayed"] = replayed
    append_tool_category_audit(tool_category_state_file, audit_record)
    if permission_pack != "creation_session":
        return
    append_tool_category_event(
        tool_category_state_file,
        creation_tool_completed_event(tool_name, arguments, result_payload),
    )
    boundary_event = record_creation_turn_write_result(
        tool_category_state_file,
        tool_name,
        result_payload,
    )
    if boundary_event is not None:
        append_tool_category_event(tool_category_state_file, boundary_event)


def _begin_scoped_workspace_step(
    db: Any,
    *,
    msg_id: Any,
    project_id: str,
    permission_pack: str | None,
    tool_category_state_file: str,
    tool_name: str,
    arguments: dict[str, Any],
    direct_mcp_lease_token: str,
) -> WorkspaceDirectMcpStepStart | None:
    if permission_pack != "project_management" or not tool_category_state_file:
        return None
    definition = registry.get(tool_name)
    if definition is None:
        raise WorkspaceDirectMcpRunLogError("Direct MCP 工具定义不存在")
    if definition not in registry.list_for_workspace_direct_mcp():
        raise WorkspaceDirectMcpRunLogError("Direct MCP 工具不在事务安全的当前作品范围内")
    return begin_workspace_direct_mcp_step(
        db,
        state_file=tool_category_state_file,
        project_id=project_id,
        tool_name=tool_name,
        arguments=arguments,
        call_id=msg_id,
        is_write=(
            definition.writes_project_data
            or definition.tool_type in {"write", "scheduler"}
        ),
        lease_token=direct_mcp_lease_token,
    )


def _payload_tool_result(tool_name: str, payload: dict[str, Any]) -> McpToolResult:
    return project_tool_result(tool_name, payload)


def _run_log_denial(tool_name: str, detail: str) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "status": "denied",
        "detail": detail,
        "data": {"reason": "direct_mcp_run_step_unavailable"},
    }


def _finish_scoped_workspace_step(
    db: Any,
    started: WorkspaceDirectMcpStepStart | None,
    result: McpToolResult,
    payload: dict[str, Any],
) -> None:
    if started is None or started.replayed:
        return
    step_model = type(started.step)
    claimed = (
        db.query(step_model)
        .filter(
            step_model.id == started.step.id,
            step_model.status == "running",
        )
        .update(
            {step_model.updated_at: step_model.updated_at},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        raise McpResultAuditError(
            "Direct MCP RunStep is no longer running at finalization"
        )
    detail = str(payload.get("detail") or "")
    finish_run_step(
        db,
        started.step,
        status=str(payload.get("status") or ("error" if result.is_error else "ok")),
        result=payload,
        detail=detail,
        error=detail if result.is_error else None,
        commit=False,
        allow_partial_commit_refs=False,
    )


def _scoped_result_audit_sink(
    db: Any,
    started: WorkspaceDirectMcpStepStart | None,
):
    if started is None or started.replayed:
        return None

    def persist(payload: dict[str, Any], result: McpToolResult) -> None:
        _finish_scoped_workspace_step(db, started, result, payload)

    return persist


def _scoped_result_audit_guard(
    db: Any,
    started: WorkspaceDirectMcpStepStart | None,
    *,
    project_id: str,
    lease_token: str,
    tool_name: str,
):
    if started is None or started.replayed:
        return None

    def validate() -> dict[str, Any] | None:
        active = cas_workspace_direct_mcp_lease(
            db,
            project_id=project_id,
            run_id=str(started.step.run_id),
            step_id=str(started.step.id),
            iteration=int(started.step.iteration or 0),
            lease_token=lease_token,
        )
        if active:
            return None
        return {
            "tool": tool_name,
            "status": "denied",
            "detail": "当前作者回合已取消、替代或失去 Direct MCP lease；未提交工具结果。",
            "data": {"reason": "turn_superseded"},
        }

    return validate


def _run_log_finalize_failure(tool_name: str, step_id: str) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "status": "error",
        "detail": "工具可能已经执行，但持久回执未能闭合；为避免重复写入，本轮不会自动重放。",
        "data": {
            "reason": "direct_mcp_run_step_finalize_failed",
            "step_id": step_id,
        },
    }


def _close_failed_scoped_workspace_step(
    db: Any,
    started: WorkspaceDirectMcpStepStart | None,
    *,
    tool_name: str,
) -> dict[str, Any]:
    step_id = str(getattr(getattr(started, "step", None), "id", ""))
    failure = _run_log_finalize_failure(tool_name, step_id)
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        rollback()
    if started is None or started.replayed:
        return failure
    step, claimed = claim_workspace_direct_mcp_step(db, started.step)
    if not claimed:
        try:
            restored = json.loads(step.result_json or "") if step is not None else None
        except (TypeError, json.JSONDecodeError):
            restored = None
        return restored if isinstance(restored, dict) else failure
    if step is None:
        db.rollback()
        return failure
    detail = str(failure["detail"])
    finish_run_step(
        db,
        step,
        status="error",
        result=failure,
        detail=detail,
        error=detail,
        allow_partial_commit_refs=False,
    )
    return failure


def _scoped_call_gate_result(
    db: Any,
    *,
    project_id: str,
    permission_pack: str | None,
    state_file: str,
    lease_token: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> McpToolResult | None:
    if not state_file:
        return None
    guarded = _turn_guard_scoped_call_result(
        db,
        project_id=project_id,
        permission_pack=permission_pack,
        tool_category_state_file=state_file,
        direct_mcp_lease_token=lease_token,
    )
    if guarded is not None:
        return guarded
    scoped = _category_scoped_call_result(tool_name, arguments, state_file)
    if scoped is not None or permission_pack != "creation_session":
        return scoped
    write_scoped = _creation_turn_write_scoped_call_result(
        tool_name, arguments, state_file
    )
    if write_scoped is not None:
        return write_scoped
    append_tool_category_event(
        state_file,
        creation_tool_started_event(tool_name, arguments),
    )
    return None


def _begin_or_replay_scoped_call(
    db: Any,
    *,
    msg_id: Any,
    project_id: str,
    permission_pack: str | None,
    state_file: str,
    lease_token: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[WorkspaceDirectMcpStepStart | None, str | None]:
    try:
        started = _begin_scoped_workspace_step(
            db,
            msg_id=msg_id,
            project_id=project_id,
            permission_pack=permission_pack,
            tool_category_state_file=state_file,
            tool_name=tool_name,
            arguments=arguments,
            direct_mcp_lease_token=lease_token,
        )
    except WorkspaceDirectMcpRunLogError as exc:
        payload = _run_log_denial(tool_name, str(exc))
        if state_file:
            _record_scoped_tool_result(
                tool_category_state_file=state_file,
                permission_pack=permission_pack,
                tool_name=tool_name,
                arguments=arguments,
                result_payload=payload,
            )
        result = _payload_tool_result(tool_name, payload)
        return None, _jsonrpc_result(msg_id, _tool_result_to_dict(result))
    if started is None or not started.replayed:
        return started, None
    payload = started.replay_result or _run_log_denial(
        tool_name, "Direct MCP 持久步骤缺少可恢复结果"
    )
    result = _payload_tool_result(tool_name, payload)
    _record_scoped_tool_result(
        tool_category_state_file=state_file,
        permission_pack=permission_pack,
        tool_name=tool_name,
        arguments=arguments,
        result_payload=payload,
        run_step=started,
        replayed=True,
    )
    return started, _jsonrpc_result(msg_id, _tool_result_to_dict(result))


def _handle_tools_call(
    msg_id: Any,
    params: dict,
    db: Any,
    project_id: str,
    allowed_tiers: set[str],
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
    direct_mcp_lease_token: str = "",
) -> str:
    """Handle one tools/call request from the blocking stdio server."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if not isinstance(arguments, dict):
        arguments = {}

    gated = _scoped_call_gate_result(
        db,
        project_id=project_id,
        permission_pack=permission_pack,
        state_file=tool_category_state_file,
        lease_token=direct_mcp_lease_token,
        tool_name=tool_name,
        arguments=arguments,
    )
    if gated is not None:
        return _jsonrpc_result(msg_id, _tool_result_to_dict(gated))

    # If no db session, return error
    if db is None:
        result = make_text_result(
            json.dumps(
                {
                    "status": "error",
                    "detail": "Database session not available for tool execution",
                }
            ),
            is_error=True,
        )
        return _jsonrpc_result(msg_id, _tool_result_to_dict(result))

    started, early_response = _begin_or_replay_scoped_call(
        db,
        msg_id=msg_id,
        project_id=project_id,
        permission_pack=permission_pack,
        state_file=tool_category_state_file,
        lease_token=direct_mcp_lease_token,
        tool_name=tool_name,
        arguments=arguments,
    )
    if early_response is not None:
        return early_response
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(execute_tool(
                db, project_id, tool_name, arguments,
                allowed_tiers=allowed_tiers,
                permission_pack=permission_pack,
                run_id=str(started.step.run_id) if started is not None else None,
                creation_session_id=creation_session_id,
                result_audit_sink=_scoped_result_audit_sink(db, started),
                result_audit_guard=_scoped_result_audit_guard(
                    db,
                    started,
                    project_id=project_id,
                    lease_token=direct_mcp_lease_token,
                    tool_name=tool_name,
                ),
            ))
        else:
            raise RuntimeError("Blocking MCP tools/call cannot run inside an event loop")
    except (McpResultAuditError, RuntimeError) as exc:
        logger.error(
            "Direct MCP RunStep finalization failed run_step=%s tool=%s type=%s",
            getattr(getattr(started, "step", None), "id", None),
            tool_name,
            type(exc).__name__,
        )
        failure = _close_failed_scoped_workspace_step(
            db, started, tool_name=tool_name
        )
        return _jsonrpc_result(
            msg_id,
            _tool_result_to_dict(_payload_tool_result(tool_name, failure)),
        )
    result_payload = tool_result_payload(result, tool_name)
    if tool_category_state_file:
        _record_scoped_tool_result(
            tool_category_state_file=tool_category_state_file,
            permission_pack=permission_pack,
            tool_name=tool_name,
            arguments=arguments,
            result_payload=result_payload,
            run_step=started,
        )
    return _jsonrpc_result(msg_id, _tool_result_to_dict(result))


def _tool_result_to_dict(result: McpToolResult) -> dict:
    """Convert McpToolResult to MCP protocol dict."""
    return {
        "content": result.content,
        "isError": result.is_error,
    }


def serve_stdio(
    *,
    db: Any = None,
    project_id: str = "",
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    creation_session_id: str = "",
    tool_category_state_file: str = "",
    direct_mcp_lease_token: str = "",
) -> None:
    """Run the MCP server over stdio (blocking).

    Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.

    Args:
        db: SQLAlchemy session for tool execution.
        project_id: Default project ID.
        allowed_tiers: Permission tiers to allow. Defaults to {"readonly"}.
        permission_pack: Permission pack name. If "auto", resolves from
            global/project settings. If a fixed pack name, uses that directly.
        creation_session_id: Session boundary required by the creation_session pack.
    """
    _configure_stdio_utf8()

    # Resolve "auto" permission pack from settings
    resolved_pack = permission_pack
    if permission_pack == "creation_session" and not creation_session_id:
        raise ValueError("creation_session permission pack requires --creation-session-id")
    if permission_pack == "creation_session" and not tool_category_state_file:
        raise ValueError("creation_session permission pack requires --tool-category-state-file")
    if (
        permission_pack == "project_management"
        and tool_category_state_file
        and not direct_mcp_lease_token
    ):
        raise ValueError("managed workspace Direct MCP requires an opaque lease token")
    if tool_category_state_file:
        read_tool_category_state(tool_category_state_file)
    managed_agent_kind = get_compatible_env("SIMING_MANAGED_AGENT_KIND").strip().lower()
    if managed_agent_kind == "cataloging" and permission_pack in {
        None,
        "auto",
        "cataloging_worker",
    }:
        resolved_pack = "cataloging_worker"
        logger.info("Managed cataloging Agent: using compact MCP permission pack")
    elif permission_pack == "auto" and db is not None:
        try:
            from app.services.external_agent.permissions import resolve_effective_pack
            result = resolve_effective_pack(db, project_id=project_id or None)
            resolved_pack = result["effective_pack"]
            logger.info(
                "Auto-resolved permission pack: %s (source: %s)",
                resolved_pack,
                result["source"],
            )
        except Exception as exc:
            logger.warning(
                "Failed to resolve auto permission pack type=%s; falling back to readonly",
                type(exc).__name__,
            )
            resolved_pack = "readonly_collaboration"

    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    stdin: TextIO = sys.stdin
    stdout: TextIO = sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_message(
            line,
            db=db,
            project_id=project_id,
            allowed_tiers=allowed_tiers,
            permission_pack=resolved_pack,
            creation_session_id=creation_session_id,
            tool_category_state_file=tool_category_state_file,
            direct_mcp_lease_token=direct_mcp_lease_token,
        )
        stdout.write(response + "\n")
        stdout.flush()
