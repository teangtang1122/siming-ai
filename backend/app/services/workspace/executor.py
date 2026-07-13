"""Dispatcher for workspace assistant tool actions."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .registry import registry


# Project-facing model generation/review tools share one prepared context
# contract. Pure rule checks and read-only retrieval are intentionally absent.
_GOVERNED_TASKS: dict[str, str] = {
    "chapter_writer": "writing",
    "character_writer": "planning",
    "outline_writer": "planning",
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


async def execute_workspace_action(
    db: Session,
    project_id: str,
    action: dict,
) -> dict:
    tool = str(action.get("tool") or "").strip()
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    if not tool:
        return {"tool": "unknown", "status": "skipped", "detail": "工具名为空"}

    handler = registry.get_handler(tool)
    if not handler:
        return {"tool": tool, "status": "skipped", "detail": "未知工具"}
    task_type = _GOVERNED_TASKS.get(tool)
    if not task_type:
        return await handler(db, project_id, args)

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
            pinned_chunk_ids=args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else (),
        )
    usable, detail = orchestrator.validate(manifest)
    if not usable:
        return {
            "tool": tool,
            "status": manifest.status if manifest.status in {"needs_confirmation", "blocked_rebuild", "stale"} else "needs_confirmation",
            "detail": detail,
            "data": {
                "context_manifest_id": manifest.id,
                "context_manifest": orchestrator.manifest_payload(manifest, include_content=False),
            },
        }
    governed_args = {**args, "context_manifest_id": manifest.id}
    with activate_context_manifest(manifest):
        result = await handler(db, project_id, governed_args)
    if result.get("status") == "ok":
        orchestrator.mark_consumed(manifest)
    if isinstance(result.get("data"), dict):
        result["data"].setdefault("context_manifest_id", manifest.id)
    return result
