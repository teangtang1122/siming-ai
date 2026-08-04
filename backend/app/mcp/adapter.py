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
from copy import deepcopy
from typing import Any

from app.services.workspace.registry import ToolDef, registry
from app.mcp.schemas import McpTool, McpToolResult, make_json_result, make_text_result
from app.mcp.permissions import filter_tools, is_allowed

logger = logging.getLogger(__name__)

# Maximum character count before content is truncated in MCP responses.
_CONTENT_TRUNCATE_LIMIT = 12000

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
    "draft_novel_blueprint",
    "review_novel_blueprint",
    "apply_novel_blueprint",
    "get_novel_creation_session",
    "get_creation_artifact",
    "list_creation_artifacts",
    "get_creation_dependencies",
    "patch_creation_artifact",
    "lock_creation_fields",
    "unlock_creation_fields",
    "undo_creation_artifact",
    "generate_novel_creation_stage",
    "submit_novel_creation_stage",
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


def _truncate_content(text: str, limit: int = _CONTENT_TRUNCATE_LIMIT) -> str:
    """Truncate content and append a notice if it exceeds the limit."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated — {len(text)} chars total]"


def _traceback_code(exc: Exception) -> str:
    """Generate a short, safe traceback identifier for logging correlation.

    Not a real stack trace — just a short hash that support can use to
    correlate client reports with server logs without exposing internals.
    """
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return hashlib.md5(tb.encode()).hexdigest()[:8]


def _suggest_next_steps(exc: Exception, tool_name: str) -> list[str]:
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
    exc: Exception,
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


def _format_tool_result(raw: dict) -> McpToolResult:
    """Convert an execute_workspace_action result dict into an MCP tool result.

    The workspace handler returns:
        {"tool": str, "status": str, "detail": str, "data": Any, "warnings": list?}

    This function:
    - Wraps it as JSON text in the MCP content array.
    - Truncates large content fields.
    - Marks errors when status indicates failure.
    """
    status = raw.get("status", "ok")
    is_error = status not in ("ok",)

    # Build the MCP-friendly payload
    payload: dict[str, Any] = {
        "status": status,
        "detail": raw.get("detail", ""),
    }
    if "tool" in raw:
        payload["tool"] = raw["tool"]
    if "data" in raw and raw["data"] is not None:
        payload["data"] = raw["data"]
    if "warnings" in raw and raw["warnings"]:
        payload["warnings"] = raw["warnings"]

    text = json.dumps(payload, ensure_ascii=False, indent=None)
    text = _truncate_content(text)

    return McpToolResult(
        content=[{"type": "text", "text": text}],
        is_error=is_error,
    )


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
        from app.services.workspace.run_log import start_run_step
        # Create a minimal step log for the MCP tool call
        # We don't have a full AssistantRun context, so we create a standalone log
        args_summary = json.dumps(arguments, ensure_ascii=False)[:500]
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


def _build_args_summary(arguments: dict[str, Any]) -> str:
    """Build a safe, truncated summary of tool arguments.

    Large content fields are replaced with placeholders.
    """
    summary_parts = []
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > 100:
            summary_parts.append(f"{key}: [{len(value)} chars]")
        elif isinstance(value, (list, dict)):
            summary_parts.append(f"{key}: [{type(value).__name__}]")
        else:
            summary_parts.append(f"{key}: {value}")
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
    except Exception:
        logger.debug("Could not infer project_id from job_id", exc_info=True)

    try:
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            from app.database.models import AgentRun

            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            inferred = getattr(run, "project_id", "") if run else ""
            if isinstance(inferred, str) and inferred.strip():
                return inferred.strip()
    except Exception:
        logger.debug("Could not infer project_id from run_id", exc_info=True)

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
        except Exception:
            logger.exception("Failed to roll back MCP database session")


def _managed_chapter_write_guard(
    db: Any,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: str | None,
) -> dict[str, Any] | None:
    """Fence a managed CLI chapter write against parent/child cancellation."""
    if tool_name not in {"create_chapter", "update_chapter"}:
        return None

    from app.database.models import (
        AgentRun,
        ChapterDraft,
        ChapterWriteClaim,
        ContextManifest,
        OperationRun,
    )
    from app.modules.operations.domain.state import ACTIVE_STATUSES
    from app.services.workspace.idempotency import validate_chapter_write_claim

    child_run = None
    manifest = None
    if run_id:
        child_run = (
            db.query(AgentRun)
            .filter(AgentRun.id == run_id, AgentRun.project_id == project_id)
            .first()
        )
        if child_run and child_run.context_manifest_id:
            manifest = (
                db.query(ContextManifest)
                .filter(
                    ContextManifest.id == child_run.context_manifest_id,
                    ContextManifest.project_id == project_id,
                )
                .first()
            )
    if manifest is None:
        manifest_id = str(arguments.get("context_manifest_id") or "").strip()
        if not manifest_id:
            draft_id = str(arguments.get("draft_id") or arguments.get("content_ref") or "").strip()
            draft = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == draft_id, ChapterDraft.project_id == project_id)
                .first()
                if draft_id
                else None
            )
            manifest_id = str(getattr(draft, "context_manifest_id", "") or "").strip()
        if manifest_id:
            manifest = (
                db.query(ContextManifest)
                .filter(
                    ContextManifest.id == manifest_id,
                    ContextManifest.project_id == project_id,
                )
                .first()
            )

    query = manifest.query_json if manifest and isinstance(manifest.query_json, dict) else {}
    contract = query.get("arguments") if isinstance(query, dict) else None
    contract = contract if isinstance(contract, dict) else {}
    if not contract.get("managed_chapter_write"):
        return None

    def denied(detail: str) -> dict[str, Any]:
        return {
            "status": "error",
            "tool": tool_name,
            "detail": detail,
            "data": {"run_id": run_id, "context_manifest_id": getattr(manifest, "id", None)},
        }

    if not run_id or child_run is None:
        return denied("受管本机 CLI 写章必须携带任务 run_id，本轮未写入章节。")
    if child_run.status not in {"created", "running"}:
        return denied(f"本机 CLI 子任务已处于 {child_run.status} 状态，本轮未写入章节。")
    child_operation = (
        db.query(OperationRun).filter(OperationRun.id == child_run.operation_id).first()
        if child_run.operation_id
        else None
    )
    if child_operation is None or child_operation.status not in ACTIVE_STATUSES:
        return denied("本机 CLI 子任务已取消、结束或中断，本轮未写入章节。")

    claim_id = str(contract.get("chapter_claim_id") or "").strip()
    claim_token = str(contract.get("chapter_claim_token") or "").strip()
    target_key = str(contract.get("chapter_target_key") or "").strip()
    idempotency_key = str(contract.get("chapter_idempotency_key") or "").strip()
    claim = (
        db.query(ChapterWriteClaim)
        .filter(ChapterWriteClaim.id == claim_id, ChapterWriteClaim.project_id == project_id)
        .first()
        if claim_id
        else None
    )
    parent_operation_id = str(contract.get("parent_operation_id") or "").strip()
    if claim and claim.operation_id:
        if parent_operation_id and parent_operation_id != claim.operation_id:
            return denied("章节写作占用与父任务不匹配，本轮未写入章节。")
        parent_operation_id = claim.operation_id
    parent_operation = (
        db.query(OperationRun).filter(OperationRun.id == parent_operation_id).first()
        if parent_operation_id
        else None
    )
    if parent_operation_id and (
        parent_operation is None or parent_operation.status not in ACTIVE_STATUSES
    ):
        return denied("父写作任务已取消、结束或中断，本轮未写入章节。")
    if not validate_chapter_write_claim(
        db,
        project_id=project_id,
        target_key=target_key,
        idempotency_key=idempotency_key,
        claim_id=claim_id,
        claim_token=claim_token,
    ):
        return denied("章节写作占用已失效或已取消，本轮未写入章节。")

    rewrite = bool(contract.get("rewrite"))
    expected_tool = "update_chapter" if rewrite else "create_chapter"
    if tool_name != expected_tool:
        return denied(
            f"当前受管任务必须调用 {expected_tool}，不能调用 {tool_name}，本轮未写入章节。"
        )
    outline_node_id = str(contract.get("outline_node_id") or "").strip()
    requested_outline = str(arguments.get("outline_node_id") or "").strip()
    if requested_outline and outline_node_id and requested_outline != outline_node_id:
        return denied("写入目标与受管章节大纲不一致，本轮未写入章节。")

    arguments["outline_node_id"] = outline_node_id
    arguments["_chapter_claim_id"] = claim_id
    arguments["_chapter_claim_token"] = claim_token
    arguments["_chapter_target_key"] = target_key
    arguments["_chapter_idempotency_key"] = idempotency_key
    if rewrite:
        arguments["rewrite"] = True
        arguments["rewrite_request_id"] = str(
            contract.get("parent_plan_id") or idempotency_key
        )
        arguments.setdefault("trigger_type", "ai_insert")
    return None


async def execute_tool(
    db: Any,
    project_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    allowed_tiers: set[str] | None = None,
    permission_pack: str | None = None,
    run_id: str | None = None,
) -> McpToolResult:
    """Execute an allowed MCP tool and return a structured MCP result.

    Args:
        db: SQLAlchemy session.
        project_id: Current project ID (from MCP client context).
        tool_name: Name of the tool to call.
        arguments: Tool arguments dict.
        allowed_tiers: Permission tiers to allow.
        permission_pack: Permission pack name. If set, overrides allowed_tiers.
        run_id: Optional external Agent run ID for automatic telemetry.

    Returns:
        McpToolResult with structured content.
    """
    if allowed_tiers is None:
        allowed_tiers = {"readonly"}

    # Validate tool exists
    td = get_tool_def(tool_name)
    if td is None:
        return make_text_result(
            json.dumps({"status": "error", "detail": f"Tool not found: {tool_name}"}),
            is_error=True,
        )

    # Validate permission
    if not is_tool_allowed(tool_name, allowed_tiers=allowed_tiers, permission_pack=permission_pack):
        return make_text_result(
            json.dumps({"status": "denied", "detail": f"Permission denied: {tool_name}"}),
            is_error=True,
        )

    # Extract project_id/run_id from arguments if present (out-of-band MCP arguments)
    effective_project_id = str(arguments.pop("project_id", "") or project_id or "").strip()
    if not effective_project_id:
        effective_project_id = _infer_project_id_from_arguments(db, arguments)
    if _requires_project_id(td) and not effective_project_id:
        return make_text_result(
            json.dumps(_missing_project_payload(tool_name), ensure_ascii=False),
            is_error=True,
        )
    telemetry_tools = {
        "report_agent_plan",
        "report_agent_progress",
        "report_context_selected",
        "append_draft_chunk",
        "mark_draft_ready",
        "finish_agent_run",
    }
    if tool_name in telemetry_tools:
        # These handlers need run_id as part of their public contract. Keep it
        # in arguments instead of consuming it as out-of-band auto-telemetry.
        run_id = run_id or str(arguments.get("run_id") or "").strip() or None
        if run_id:
            arguments["run_id"] = run_id
    elif not run_id:
        run_id = arguments.pop("run_id", None)
    else:
        arguments.pop("run_id", None)  # Strip if passed explicitly

    managed_write_error = _managed_chapter_write_guard(
        db,
        effective_project_id,
        tool_name,
        arguments,
        str(run_id or "").strip() or None,
    )
    if managed_write_error:
        if run_id:
            _log_run_tool_event(
                db,
                str(run_id),
                "tool_result",
                tool_name,
                arguments,
                status="error",
                detail=str(managed_write_error.get("detail") or "受管写章校验失败"),
            )
        return make_text_result(
            json.dumps(managed_write_error, ensure_ascii=False),
            is_error=True,
        )

    # Workspace handlers use this marker to distinguish an author/manual API
    # save from a formal write initiated by an MCP client.  The latter must
    # carry a governed manifest and verified evidence before it can become
    # project state.  It is deliberately internal-only and never part of the
    # public tool schema.
    arguments.setdefault("_context_execution_route", "external_mcp")

    # Check confirmation token for legacy write tiers and explicitly sensitive tools.
    # Trusted local mode is intentionally frictionless: it can execute Siming MCP
    # project tools without an extra frontend confirmation prompt. Secret/internal
    # model tools remain excluded by the permission-pack registry boundary.
    from app.mcp.permissions import get_tier, validate_confirmation_token
    trusted_local = permission_pack == "trusted_local_maintenance"
    requires_token = (get_tier(td) == "write_confirmed" and not permission_pack) or td.requires_confirmation
    if requires_token and not trusted_local:
        token_str = arguments.pop("confirmation_token", "")
        is_valid, reason = validate_confirmation_token(token_str, tool_name)
        if not is_valid:
            return make_text_result(
                json.dumps({
                    "status": "denied",
                    "detail": f"Write confirmation required: {reason}",
                    "reason": reason,
                }),
                is_error=True,
            )

    # Log tool_start event if run_id is provided
    if run_id:
        _log_run_tool_event(db, run_id, "tool_start", tool_name, arguments, status="running")

    # Execute through the existing workspace executor
    try:
        from app.services.workspace.executor import execute_workspace_action
        raw_result = await execute_workspace_action(
            db,
            effective_project_id,
            {"tool": tool_name, "arguments": arguments},
        )
        if raw_result.get("status", "ok") == "ok":
            try:
                _safe_commit(db)
            except Exception:
                _safe_rollback(db)
                raise
        else:
            _safe_rollback(db)

        # Log the MCP tool call
        _log_mcp_tool_call(
            db, effective_project_id, tool_name, arguments,
            status=raw_result.get("status", "ok"),
            detail=raw_result.get("detail", ""),
        )

        # Log tool_result event if run_id is provided
        if run_id:
            _log_run_tool_event(
                db, run_id, "tool_result", tool_name, arguments,
                status=raw_result.get("status", "ok"),
                detail=raw_result.get("detail", ""),
            )

        return _format_tool_result(raw_result)
    except Exception as exc:
        _safe_rollback(db)
        logger.exception("MCP tool execution failed: %s", tool_name)

        error_payload = _build_error_payload(tool_name=tool_name, exc=exc)

        # Log the failure
        _log_mcp_tool_call(
            db, effective_project_id, tool_name, arguments,
            status="error",
            detail=error_payload["detail"],
        )

        # Log tool_result error event if run_id is provided
        if run_id:
            _log_run_tool_event(
                db, run_id, "tool_result", tool_name, arguments,
                status="error",
                detail=error_payload["detail"],
            )

        return make_text_result(
            json.dumps(error_payload, ensure_ascii=False),
            is_error=True,
        )
