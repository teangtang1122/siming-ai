"""MCP adapter — bridges ToolRegistry to MCP protocol.

Reads ToolDef entries from the existing registry singleton,
applies permission filtering, and converts to MCP schema format.
Does NOT modify the ToolRegistry.
"""
from __future__ import annotations

import hashlib
import json
import logging
import traceback
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.architecture.uow import defer_session_commits
from app.mcp.permissions import filter_tools, is_allowed
from app.mcp.schemas import McpTool, McpToolResult, make_text_result
from app.services.workspace.registry import ToolDef, registry
from app.services.workspace.tool_result_projection import (
    ToolResultProjectionError,
    model_tool_result_projector,
    sanitize_diagnostic_tool_result,
)

logger = logging.getLogger(__name__)

# Workspace tools usually return ``ok``.  Context governance additionally
# exposes the persisted manifest lifecycle states below; both mean the result
# is usable and must not be surfaced to MCP clients as a failed tool call.
MCP_USABLE_STATUSES = frozenset({
    "ok",
    "ready",
    "overridden",
    "awaiting_selection",
    "selected",
    "consumed",
})

_TELEMETRY_TOOLS = frozenset({
    "report_agent_plan",
    "report_agent_progress",
    "report_context_selected",
    "append_draft_chunk",
    "mark_draft_ready",
    "finish_agent_run",
})
_RUN_BOUND_CONTEXT_TOOLS = frozenset({
    "prepare_task_context",
    "search_task_context",
    "submit_context_evidence",
})


class McpResultAuditError(Exception):
    """The server could not persist the authoritative result of an MCP call."""


@dataclass(frozen=True)
class _PreparedToolExecution:
    definition: ToolDef
    project_id: str
    run_id: str | None

_PROJECT_OPTIONAL_TOOLS = {
    "list_projects",
    "create_project",
    "get_project_info",
    "update_project_info",
    "delete_project",
    "import_file_as_project",
    "preview_import_splits",
    "web_search",
    "get_mcp_permission_status",
    "get_moshu_usage_guide",
    "list_prompt_packs",
    "get_prompt_pack",
    "get_tool_playbook",
    "get_quality_rubric",
    "list_skill_templates",
    "start_novel_creation_session",
    "get_creation_artifact",
    "list_creation_artifacts",
    "get_creation_dependencies",
    "patch_creation_artifact",
    "lock_creation_fields",
    "unlock_creation_fields",
    "undo_creation_artifact",
    "get_creation_session",
    "get_creation_snapshot",
    "get_creation_operation",
    "get_creation_dependency_graph",
    "validate_creation_consistency",
    "patch_creation_session",
    "list_creation_entities",
    "get_creation_entity",
    "patch_creation_entity",
    "delete_creation_entity",
    "list_creation_artifact_versions",
    "get_creation_artifact_diff",
    "restore_creation_artifact_version",
    "confirm_creation_artifact",
    "generate_creation_artifact",
    "refine_creation_artifact",
    "regenerate_creation_artifact",
    "cancel_creation_operation",
    "pause_creation_operation",
    "resume_creation_operation",
    "retry_creation_operation",
    "validate_creation_session",
    "finalize_creation_session",
    "preview_creation_import",
    "apply_creation_import",
    "list_imported_files",
    "read_imported_file",
}


def _requires_project_id(td: ToolDef) -> bool:
    """Whether an MCP tool needs an explicit or inferred project target."""
    return td.name not in _PROJECT_OPTIONAL_TOOLS


def _add_project_id_argument(tool: McpTool, *, required: bool = False) -> McpTool:
    """Expose universal project and run context for MCP clients.

    Workspace handlers receive project_id out-of-band from the internal UI. MCP
    clients often operate globally, so they need an explicit way to target a
    project after calling list_projects. run_id lets managed Agents attach tool
    calls and progress events to the frontend-visible Agent run.
    """
    schema = deepcopy(tool.input_schema)
    properties = schema.setdefault("properties", {})
    properties.setdefault("project_id", {
        "type": "string",
        "description": (
            "Target project ID. Call list_projects or use the project_id returned by "
            "create_project/import_file_as_project. Project-scoped tools must use the "
            "same project_id for every read/write/verify step."
        ),
    })
    properties.setdefault("run_id", {
        "type": "string",
        "description": (
            "Optional Siming Agent run ID. Managed local/external Agents should pass "
            "the run_id supplied by their task so tool calls and progress appear in "
            "the frontend execution timeline."
        ),
    })
    if required:
        required_list = list(schema.get("required") or [])
        if "project_id" not in required_list:
            required_list.append("project_id")
        schema["required"] = required_list
    return McpTool(name=tool.name, description=tool.description, input_schema=schema)


def list_mcp_tools(
    *,
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
) -> list[McpTool]:
    """Return MCP-formatted tool list, filtered by permission tier or pack.

    Args:
        allowed_tiers: Tier names to allow (legacy). Defaults to {"readonly"}.
        permission_pack: Permission pack name. If set, overrides allowed_tiers.
    """
    if permission_pack:
        allowed_defs = registry.list_for_mcp(permission_pack=permission_pack)
    else:
        if allowed_tiers is None:
            allowed_tiers = {"readonly"}
        all_defs: list[ToolDef] = []
        for name in registry.all_names():
            td = registry.get(name)
            if td is not None:
                all_defs.append(td)
        allowed_defs = filter_tools(all_defs, allowed_tiers=allowed_tiers)

    result: list[McpTool] = []
    for td in allowed_defs:
        spec = registry.get_spec(td.name)
        if spec is None:
            continue
        result.append(_add_project_id_argument(McpTool(
            name=td.name,
            description=td.description,
            input_schema=spec.parameters_schema(),
        ), required=_requires_project_id(td)))
    return result


def get_tool_def(name: str) -> ToolDef | None:
    """Look up a ToolDef by name."""
    return registry.get(name)


def is_tool_allowed(
    name: str,
    *,
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
) -> bool:
    """Check whether a tool is allowed under the given tiers or pack."""
    td = registry.get(name)
    if td is None:
        return False

    if permission_pack:
        if not td.expose_to_mcp:
            return False
        allowed_tools = registry.list_for_mcp(permission_pack=permission_pack)
        return td in allowed_tools

    return is_allowed(td, allowed_tiers=allowed_tiers)


def _traceback_code(exc: BaseException) -> str:
    """Generate a short, safe traceback identifier for logging correlation.

    Not a real stack trace — just a short hash that support can use to
    correlate client reports with server logs without exposing internals.
    """
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return hashlib.md5(tb.encode()).hexdigest()[:8]


def _suggest_next_steps(exc: BaseException, tool_name: str) -> list[str]:
    """Return actionable suggestions for recoverable error types."""
    exc_type = type(exc).__name__

    if exc_type == "PendingRollbackError":
        return [
            "Database session is in a failed state. Retry the last tool call.",
            "If this persists, restart the MCP server process.",
        ]
    if exc_type == "IntegrityError":
        return [
            "A data constraint was violated. Check for duplicate entries or missing references.",
        ]
    if exc_type == "OperationalError":
        return [
            "Database connection issue. Verify the database file is accessible.",
        ]
    if "timeout" in str(exc).lower() or exc_type == "TimeoutError":
        return [
            "The operation timed out. Try with fewer items or a smaller request.",
        ]
    return []


def _build_error_payload(
    *,
    tool_name: str,
    exc: BaseException,
    detail: str = "",
) -> dict:
    """Build a structured MCP error response with actionable details."""
    exc_type = type(exc).__name__
    tb_code = _traceback_code(exc)
    suggestions = _suggest_next_steps(exc, tool_name)

    # PendingRollbackError gets a specific, non-generic message
    if exc_type == "PendingRollbackError":
        effective_detail = (
            "Database session rolled back unexpectedly. "
            "The previous operation may have failed. Retry the last call."
        )
    else:
        effective_detail = detail or f"Tool execution failed: {exc_type}"

    payload: dict[str, Any] = {
        "status": "error",
        "tool": tool_name,
        "detail": effective_detail,
        "error_type": exc_type,
        "traceback_code": tb_code,
    }
    if suggestions:
        payload["next_suggestions"] = suggestions
    return payload


def _project_tool_execution(
    td: ToolDef,
    raw: Mapping[str, Any],
) -> tuple[McpToolResult, dict[str, Any]]:
    """Return the model projection and the authoritative persistence result.

    MCP clients are model runtimes too, so they receive the same declarative
    projection as native assistant loops. Results are never character-sliced:
    an oversized or non-JSON source becomes a small, valid JSON error receipt.
    """
    try:
        projected = model_tool_result_projector.project(td, raw)
        status_value = projected.payload.get("status")
        if not isinstance(status_value, str) or not status_value.strip():
            raise ToolResultProjectionError(td.name, "工具结果缺少有效的 status")
    except Exception as exc:
        if isinstance(exc, ToolResultProjectionError):
            unsafe_error = exc.model_error_result()
        else:
            # RecursionError and unexpected serializer failures are not
            # ToolResultProjectionError subclasses. Do not reflect their text:
            # a handler-controlled object may have produced it.
            unsafe_error = {
                "tool": td.name,
                "status": "error",
                "detail": "工具结果无法按声明的模型可见合同序列化。",
                "data": {"reason": "serialization_failed"},
            }
        audit_result = sanitize_diagnostic_tool_result(td.name, unsafe_error)
        status = str(audit_result.get("status") or "error").strip().lower()
        return (
            McpToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps(audit_result, ensure_ascii=False),
                }],
                is_error=status not in MCP_USABLE_STATUSES,
            ),
            audit_result,
        )
    else:
        audit_result = dict(raw)

    status = str(projected.payload["status"]).strip().lower()
    return (
        McpToolResult(
            content=[{"type": "text", "text": projected.content}],
            is_error=status not in MCP_USABLE_STATUSES,
        ),
        audit_result,
    )


def _format_tool_result(td: ToolDef, raw: Mapping[str, Any]) -> McpToolResult:
    """Project one handler result through its declared model-visible contract."""

    return _project_tool_execution(td, raw)[0]


def project_tool_result(tool_name: str, raw: Mapping[str, Any]) -> McpToolResult:
    """Project a persisted result through the same contract used for live MCP."""

    definition = get_tool_def(tool_name)
    if definition is None:
        return make_text_result(
            json.dumps(
                {
                    "tool": tool_name,
                    "status": "error",
                    "detail": "MCP 工具定义不存在，无法恢复持久结果。",
                },
                ensure_ascii=False,
            ),
            is_error=True,
        )
    return _format_tool_result(definition, raw)


def tool_result_payload(result: McpToolResult, tool_name: str) -> dict[str, Any]:
    """Parse a structured MCP result for auditing without inventing success."""

    for block in result.content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except (json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        claimed_tool = payload.get("tool")
        if not isinstance(status, str) or not status.strip():
            continue
        if claimed_tool not in (None, "", tool_name):
            continue
        return payload
    return {
        "tool": tool_name,
        "status": "error",
        "detail": "MCP 工具结果不是合法结构化 JSON。",
        "data": {"reason": "serialization_failed"},
    }


def _complete_preflight_result(
    db: Any,
    tool_name: str,
    result: McpToolResult,
    result_audit_sink: Callable[[dict[str, Any], McpToolResult], None] | None,
) -> McpToolResult:
    """Close a pre-handler audit step without committing business mutations."""

    if result_audit_sink is None:
        return result
    _safe_rollback(db)
    try:
        result_audit_sink(tool_result_payload(result, tool_name), result)
        _safe_commit(db)
    except Exception as exc:
        _safe_rollback(db)
        raise McpResultAuditError("MCP preflight result audit failed") from exc
    return result


def _log_mcp_tool_call(
    db: Any,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    status: str,
    detail: str,
) -> None:
    """Log an MCP tool call to the run log system.

    Creates a lightweight log entry that records the MCP tool name,
    arguments summary, execution status, and any error details.
    """
    try:
        # Create a minimal step log for the MCP tool call
        # We don't have a full AssistantRun context, so we create a standalone log
        args_summary = _build_args_summary(arguments)
        logger.info(
            "MCP tool call: tool=%s project=%s status=%s args=%s",
            tool_name, project_id, status, args_summary,
        )
        # If there's an active assistant run in the session context,
        # we could attach to it. For now, log via the standard logger.
        # The run_log integration will be completed when MCP tools are
        # called from within an assistant conversation.
    except Exception:
        # Logging should never break tool execution
        pass


def _log_run_tool_event(
    db: Any,
    run_id: str,
    event_type: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    status: str = "ok",
    detail: str = "",
) -> None:
    """Log a tool_start or tool_result event to an external Agent run.

    This is a best-effort operation — failures are logged but never
    break tool execution.
    """
    try:
        from app.services.external_agent.run_service import add_event

        # Build safe argument summary (no full content)
        args_summary = _build_args_summary(arguments)

        payload = {
            "tool": tool_name,
            "args_summary": args_summary,
        }
        if detail:
            payload["detail"] = detail[:500]

        add_event(
            db, run_id, event_type,
            status=status,
            message=f"{tool_name}: {detail[:100]}" if detail else tool_name,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    except Exception:
        # Telemetry must never break tool execution
        pass


_REDACTED_ARGUMENT_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "body",
    "confirmationtoken",
    "content",
    "contextselectiontoken",
    "cwd",
    "directmcpleasetoken",
    "directory",
    "filecontent",
    "filepath",
    "leasetoken",
    "manuscript",
    "oauthtoken",
    "password",
    "path",
    "prompt",
    "projectfolder",
    "secret",
    "selectedtext",
    "sourcepath",
    "targetpath",
    "text",
    "token",
}


def _normalized_argument_key(key: Any) -> str:
    return "".join(char for char in str(key).lower() if char.isalnum())


def _safe_argument_log_key(key: Any) -> str:
    raw = str(key)
    identifier = raw.replace("_", "")
    if raw and len(raw) <= 64 and raw.isascii() and identifier.isalnum():
        return raw
    return "[field]"


def _build_args_summary(arguments: dict[str, Any]) -> str:
    """Build a bounded structural summary without logging string payloads."""
    summary_parts = []
    for key, value in arguments.items():
        if _normalized_argument_key(key) in _REDACTED_ARGUMENT_KEYS:
            rendered = "[redacted]"
        elif isinstance(value, str):
            rendered = f"[str:{len(value)}]"
        elif isinstance(value, (list, dict)):
            rendered = f"[{type(value).__name__}:{len(value)}]"
        else:
            rendered = str(value)
        summary_parts.append(f"{_safe_argument_log_key(key)}: {rendered}")
    result = ", ".join(summary_parts)
    return result[:300] if len(result) > 300 else result


def _infer_project_id_from_arguments(db: Any, arguments: dict[str, Any]) -> str:
    """Infer a project ID from stable workflow IDs when possible."""
    try:
        job_id = str(arguments.get("job_id") or "").strip()
        if job_id:
            from app.database.models import CatalogingJob

            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            inferred = getattr(job, "project_id", "") if job else ""
            if isinstance(inferred, str) and inferred.strip():
                return inferred.strip()
    except Exception as exc:
        logger.debug(
            "Could not infer project_id from job_id type=%s traceback_code=%s",
            type(exc).__name__,
            _traceback_code(exc),
        )

    try:
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            from app.database.models import AgentRun

            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            inferred = getattr(run, "project_id", "") if run else ""
            if isinstance(inferred, str) and inferred.strip():
                return inferred.strip()
    except Exception as exc:
        logger.debug(
            "Could not infer project_id from run_id type=%s traceback_code=%s",
            type(exc).__name__,
            _traceback_code(exc),
        )

    return ""


def _missing_project_payload(tool_name: str) -> dict[str, Any]:
    return {
        "status": "denied",
        "tool": tool_name,
        "detail": (
            "project_id is required for this Siming tool. Call list_projects or "
            "use the project_id returned by create_project/import_file_as_project, "
            "then pass that same project_id to every project-scoped tool call."
        ),
        "workflow_reminder": {
            "required_arg": "project_id",
            "standard_flow": [
                "list_projects or import_file_as_project",
                "record data.id as project_id",
                "call project-scoped tools with project_id",
                "verify with get_project_archive_status(project_id=...)",
            ],
        },
    }


def _safe_commit(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


def _safe_rollback(db: Any) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception as exc:
            logger.error(
                "Failed to roll back MCP database session type=%s traceback_code=%s",
                type(exc).__name__,
                _traceback_code(exc),
            )


def _creation_session_scope_error(
    db: Any,
    td: ToolDef,
    tool_name: str,
    arguments: dict[str, Any],
    creation_session_id: str,
) -> str | None:
    """Bind a transient creation MCP process to exactly one draft session."""

    bound_id = str(creation_session_id or "").strip()
    if not bound_id:
        return "Creation-session MCP is missing its required session binding"

    # Session-addressed tools do not need the model to repeat an opaque ID and
    # cannot be redirected to a different draft.
    if "session_id" in (td.input_schema or {}):
        supplied = str(arguments.get("session_id") or "").strip()
        if supplied and supplied != bound_id:
            return f"Creation session scope mismatch for {tool_name}"
        arguments["session_id"] = bound_id

    from app.modules.creation.infrastructure.models import (
        NovelCreationArtifactVersion,
        NovelCreationEntity,
        NovelCreationMaterialImport,
        NovelCreationStageRun,
    )

    def _record_session(model: Any, record_id: str) -> str:
        record = db.get(model, record_id) if record_id else None
        return str(getattr(record, "session_id", "") or "")

    entity_id = str(arguments.get("entity_id") or "").strip()
    if entity_id and _record_session(NovelCreationEntity, entity_id) != bound_id:
        return f"Creation entity is outside the authorized session: {entity_id}"

    for key in ("version_id", "against_version_id"):
        version_id = str(arguments.get(key) or "").strip()
        if version_id and _record_session(NovelCreationArtifactVersion, version_id) != bound_id:
            return f"Creation artifact version is outside the authorized session: {version_id}"

    import_id = str(arguments.get("import_id") or "").strip()
    if import_id and _record_session(NovelCreationMaterialImport, import_id) != bound_id:
        return f"Creation import is outside the authorized session: {import_id}"

    run_id = str(arguments.get("run_id") or "").strip()
    if run_id and _record_session(NovelCreationStageRun, run_id) != bound_id:
        return f"Creation run is outside the authorized session: {run_id}"

    operation_id = str(arguments.get("operation_id") or "").strip()
    if operation_id:
        stage_run = db.query(NovelCreationStageRun).filter(
            NovelCreationStageRun.operation_id == operation_id,
            NovelCreationStageRun.session_id == bound_id,
        ).first()
        material_import = db.query(NovelCreationMaterialImport).filter(
            NovelCreationMaterialImport.operation_id == operation_id,
            NovelCreationMaterialImport.session_id == bound_id,
        ).first()
        if not stage_run and not material_import:
            return f"Creation operation is outside the authorized session: {operation_id}"

    # Creation tools do not need an out-of-band project selector. Dropping it
    # prevents a model from widening a creation-only turn into project access.
    arguments.pop("project_id", None)
    return None


def _error_result(payload: Mapping[str, Any]) -> McpToolResult:
    return make_text_result(json.dumps(dict(payload), ensure_ascii=False), is_error=True)


def _prepare_tool_execution(
    db: Any,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    allowed_tiers: set[str],
    permission_pack: str | None,
    run_id: str | None,
    creation_session_id: str,
) -> _PreparedToolExecution | McpToolResult:
    """Validate scope and normalize internal arguments before dispatch."""

    td = get_tool_def(tool_name)
    if td is None:
        return _error_result(
            {"status": "error", "detail": f"Tool not found: {tool_name}"}
        )
    if not is_tool_allowed(tool_name, allowed_tiers=allowed_tiers, permission_pack=permission_pack):
        return _error_result(
            {"status": "denied", "detail": f"Permission denied: {tool_name}"}
        )
    if permission_pack == "creation_session":
        scope_error = _creation_session_scope_error(
            db,
            td,
            tool_name,
            arguments,
            creation_session_id,
        )
        if scope_error:
            return _error_result({"status": "denied", "detail": scope_error})
    effective_project_id = str(arguments.pop("project_id", "") or project_id or "").strip()
    if not effective_project_id:
        effective_project_id = _infer_project_id_from_arguments(db, arguments)
    if _requires_project_id(td) and not effective_project_id:
        return _error_result(_missing_project_payload(tool_name))
    if tool_name in _TELEMETRY_TOOLS or tool_name in _RUN_BOUND_CONTEXT_TOOLS:
        run_id = run_id or str(arguments.get("run_id") or "").strip() or None
        if run_id:
            arguments["run_id"] = run_id
    elif not run_id:
        run_id = arguments.pop("run_id", None)
    else:
        arguments.pop("run_id", None)
    arguments.setdefault("_context_execution_route", "external_mcp")
    from app.mcp.permissions import get_tier, validate_confirmation_token

    trusted_local = permission_pack == "trusted_local_maintenance"
    requires_token = (
        get_tier(td) == "write_confirmed" and not permission_pack
    ) or td.requires_confirmation
    if requires_token and not trusted_local:
        token_str = arguments.pop("confirmation_token", "")
        is_valid, reason = validate_confirmation_token(token_str, tool_name)
        if not is_valid:
            return _error_result(
                {
                    "status": "denied",
                    "detail": f"Write confirmation required: {reason}",
                    "reason": reason,
                }
            )
    return _PreparedToolExecution(td, effective_project_id, run_id)


async def _execute_prepared_tool(
    db: Any,
    prepared: _PreparedToolExecution,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[McpToolResult, dict[str, Any]]:
    from app.services.workspace.executor import execute_workspace_action

    raw_result = await execute_workspace_action(
        db,
        prepared.project_id,
        {"tool": tool_name, "arguments": arguments},
    )
    return _project_tool_execution(prepared.definition, raw_result)


def _log_execution_result(
    db: Any,
    prepared: _PreparedToolExecution,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    status: str,
    detail: str,
) -> None:
    _log_mcp_tool_call(
        db,
        prepared.project_id,
        tool_name,
        arguments,
        status=status,
        detail=detail,
    )
    if prepared.run_id:
        _log_run_tool_event(
            db,
            prepared.run_id,
            "tool_result",
            tool_name,
            arguments,
            status=status,
            detail=detail,
        )


async def execute_tool(
    db: Any,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    run_id: str | None = None,
    creation_session_id: str = "",
    result_audit_sink: Callable[[dict[str, Any], McpToolResult], None] | None = None,
    result_audit_guard: Callable[[], dict[str, Any] | None] | None = None,
) -> McpToolResult:
    """Execute one allowed MCP tool under one auditable commit boundary."""

    prepared = _prepare_tool_execution(
        db,
        project_id,
        tool_name,
        arguments,
        allowed_tiers=allowed_tiers or {"readonly"},
        permission_pack=permission_pack,
        run_id=run_id,
        creation_session_id=creation_session_id,
    )
    if isinstance(prepared, McpToolResult):
        return _complete_preflight_result(
            db, tool_name, prepared, result_audit_sink
        )

    if prepared.run_id:
        _log_run_tool_event(
            db, prepared.run_id, "tool_start", tool_name, arguments, status="running"
        )
    try:
        trusted_boundary = result_audit_sink is not None and result_audit_guard is not None
        commit_scope = defer_session_commits(db) if trusted_boundary else nullcontext()
        with commit_scope:
            formatted_result, audit_result = await _execute_prepared_tool(
                db, prepared, tool_name, arguments
            )
            result_payload = tool_result_payload(formatted_result, tool_name)
            result_status = str(result_payload["status"]).strip().lower()
            usable = not formatted_result.is_error and result_status in MCP_USABLE_STATUSES
            if usable and result_audit_guard is not None:
                rejection = result_audit_guard()
                if rejection is not None:
                    formatted_result, audit_result = _project_tool_execution(
                        prepared.definition, rejection
                    )
                    result_payload = tool_result_payload(formatted_result, tool_name)
                    result_status = str(result_payload["status"]).strip().lower()
                    usable = False
            if not usable:
                _safe_rollback(db)
                formatted_result = _complete_preflight_result(
                    db, tool_name, formatted_result, result_audit_sink
                )
            else:
                if result_audit_sink is not None:
                    result_audit_sink(audit_result, formatted_result)
                try:
                    _safe_commit(db)
                except Exception as exc:
                    _safe_rollback(db)
                    raise McpResultAuditError(
                        "MCP authoritative result commit failed"
                    ) from exc
        detail = str(result_payload.get("detail") or "")
        _log_execution_result(
            db,
            prepared,
            tool_name,
            arguments,
            status=result_status,
            detail=detail,
        )
        return formatted_result
    except McpResultAuditError:
        _safe_rollback(db)
        raise
    except BaseException as exc:
        _safe_rollback(db)
        logger.error(
            "MCP tool execution failed tool=%s type=%s traceback_code=%s",
            tool_name,
            type(exc).__name__,
            _traceback_code(exc),
        )
        cancelled = not isinstance(exc, Exception)
        error_payload = (
            {
                "tool": tool_name,
                "status": "cancelled",
                "detail": "MCP 工具执行已取消。",
                "data": {"reason": "execution_cancelled"},
            }
            if cancelled
            else _build_error_payload(tool_name=tool_name, exc=exc)
        )
        error_result = _complete_preflight_result(
            db,
            tool_name,
            _error_result(error_payload),
            result_audit_sink,
        )
        _log_execution_result(
            db,
            prepared,
            tool_name,
            arguments,
            status=str(error_payload["status"]),
            detail=error_payload["detail"],
        )
        if cancelled:
            raise
        return error_result
