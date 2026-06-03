"""Dispatcher for workspace assistant tool actions."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .registry import registry


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
    return await handler(db, project_id, args)
