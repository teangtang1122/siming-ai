"""Dispatcher for workspace assistant tool actions."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .tools import (
    create_chapter,
    create_character,
    create_outline_node,
    create_relationship,
    create_worldbuilding_entry,
    update_character,
    update_outline_node,
    update_worldbuilding_entry,
)
from .types import ToolHandler, WorkspaceActionDependencies


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "create_worldbuilding_entry": create_worldbuilding_entry,
    "update_worldbuilding_entry": update_worldbuilding_entry,
    "create_character": create_character,
    "update_character": update_character,
    "create_relationship": create_relationship,
    "create_outline_node": create_outline_node,
    "update_outline_node": update_outline_node,
    "create_chapter": create_chapter,
}


async def execute_workspace_action(
    db: Session,
    project_id: str,
    action: dict,
    deps: WorkspaceActionDependencies,
) -> dict:
    tool = str(action.get("tool") or "").strip()
    args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    if not tool:
        return {"tool": "unknown", "status": "skipped", "detail": "工具名为空"}

    handler = TOOL_HANDLERS.get(tool)
    if not handler:
        return {"tool": tool, "status": "skipped", "detail": "未知工具"}
    return await handler(db, project_id, args, deps)

