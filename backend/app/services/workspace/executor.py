"""Dispatcher for workspace assistant tool actions."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.architecture.tool_spec import ToolInputSchemaValidationError
from app.architecture.uow import commit_session

from .registry import registry
from .tool_result_projection import sanitize_diagnostic_tool_result

# Project-facing model generation/review tools share one prepared context
# contract. Pure rule checks and read-only retrieval are intentionally absent.
_GOVERNED_TASKS: dict[str, str] = {
    "chapter_writer": "writing",
    "character_writer": "planning",
    "outline_writer": "outline_planning",
    "worldbuilding_writer": "planning",
    "design_plot": "planning",
    "roleplay_character": "writing",
    "dialogue_battle": "writing",
    "rewrite_text": "rewrite",
    "expand_text": "rewrite",
    "continue_text": "rewrite",
    "detect_character_changes": "review",
    "detect_new_worldbuilding": "review",
    "detect_worldbuilding_conflicts": "review",
    "evaluate_chapter": "review",
    "suggest_conflicts": "review",
}


def _validation_location(parts: tuple[Any, ...]) -> str:
    location = "$"
    for part in parts:
        location += f"[{part}]" if isinstance(part, int) else f".{part}"
    return location


def _invalid_arguments_result(
    tool: str,
    error: ToolInputSchemaValidationError | PydanticValidationError,
) -> dict[str, Any]:
    if isinstance(error, ToolInputSchemaValidationError):
        detail = error.public_detail
        path = _validation_location(error.path)
        rule = error.rule or "schema"
    else:
        first = error.errors(include_input=False, include_url=False)[0]
        path_parts = tuple(first.get("loc") or ())
        path = _validation_location(path_parts)
        rule = str(first.get("type") or "schema")
        detail = f"{path}：{first.get('msg') or '参数无效'}"
    return {
        "tool": tool,
        "status": "error",
        "detail": f"工具参数不符合 {tool} 的定义：{detail}",
        "data": {
            "reason": "native_tool_contract_invalid",
            "failure_class": "invalid_tool_arguments",
            "path": path,
            "rule": rule,
            "retryable": True,
        },
    }


async def execute_workspace_action(
    db: Session,
    project_id: str,
    action: dict,
) -> dict:
    tool = str(action.get("tool") or "").strip()
    if not tool:
        return {"tool": "unknown", "status": "skipped", "detail": "工具名为空"}

    handler = registry.get_handler(tool)
    if not handler:
        return {"tool": tool, "status": "skipped", "detail": "未知工具"}
    raw_args = action.get("arguments")
    args = {} if raw_args is None else raw_args
    spec = registry.get_spec(tool)
    if spec is not None:
        try:
            args = spec.validate_input(args).model_dump(exclude_unset=True)
        except (ToolInputSchemaValidationError, PydanticValidationError) as exc:
            return _invalid_arguments_result(tool, exc)
    task_type = _GOVERNED_TASKS.get(tool)
    if not task_type:
        result = await handler(db, project_id, args)
        return sanitize_diagnostic_tool_result(tool, result)

    if tool in {"chapter_writer", "outline_writer"}:
        # Both authoring generators validate an explicit, model-finalized manifest and its
        # one-step selection token. Never recreate a default manifest here:
        # doing so would bypass the Agent retrieval/review workflow.
        result = await handler(db, project_id, args)
        return sanitize_diagnostic_tool_result(tool, result)

    from ..context_orchestrator import ContextOrchestrator, activate_context_manifest

    orchestrator = ContextOrchestrator(db)
    manifest_id = str(args.get("context_manifest_id") or "").strip()
    manifest = orchestrator.get_manifest(manifest_id, project_id) if manifest_id else None
    if manifest is None:
        manifest = orchestrator.prepare(
            project_id=project_id,
            task_type=task_type,
            model=str(args.get("model") or "") or None,
            execution_route="workspace_internal",
            arguments=args,
            pinned_chunk_ids=(
                args.get("pinned_chunk_ids")
                if isinstance(args.get("pinned_chunk_ids"), list)
                else ()
            ),
        )
    usable, detail = orchestrator.validate(manifest)
    if not usable:
        # Validation may mark the manifest stale. Persist that short mutation
        # before returning so no writer lease leaks into caller-side work.
        commit_session(db)
        return {
            "tool": tool,
            "status": (
                manifest.status
                if manifest.status in {"needs_confirmation", "blocked_rebuild", "stale"}
                else "needs_confirmation"
            ),
            "detail": detail,
            "data": {
                "context_manifest_id": manifest.id,
                "context_manifest": orchestrator.manifest_payload(manifest, include_content=False),
            },
        }
    governed_args = {**args, "context_manifest_id": manifest.id}
    with activate_context_manifest(manifest):
        # prepare()/validate() both flush audit state. The governed handler may
        # await a remote model for minutes, so close this transaction first.
        commit_session(db)
        result = await handler(db, project_id, governed_args)
    result = sanitize_diagnostic_tool_result(tool, result)
    if result.get("status") == "ok":
        orchestrator.mark_consumed(manifest)
        commit_session(db)
    if isinstance(result.get("data"), dict):
        result["data"].setdefault("context_manifest_id", manifest.id)
    return result
