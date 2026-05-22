"""Dispatcher for workspace assistant tool actions."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .tools import (
    continue_text,
    create_chapter,
    create_character,
    create_outline_node,
    create_relationship,
    create_worldbuilding_entry,
    delete_chapter,
    delete_character,
    delete_outline_node,
    delete_relationship,
    delete_worldbuilding_entry,
    detect_character_changes,
    detect_worldbuilding_conflicts,
    dialogue_battle,
    expand_text,
    list_characters,
    list_chapters,
    list_worldbuilding,
    rewrite_text,
    roleplay_character,
    search_characters,
    search_chapters,
    search_outline,
    search_outline_tree,
    search_relationships,
    search_worldbuilding,
    suggest_conflicts,
    update_chapter,
    update_character,
    update_outline_node,
    update_relationship,
    update_worldbuilding_entry,
)
from .types import ToolHandler


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "create_chapter": create_chapter,
    "update_chapter": update_chapter,
    "delete_chapter": delete_chapter,
    "create_character": create_character,
    "update_character": update_character,
    "delete_character": delete_character,
    "create_outline_node": create_outline_node,
    "update_outline_node": update_outline_node,
    "delete_outline_node": delete_outline_node,
    "create_relationship": create_relationship,
    "update_relationship": update_relationship,
    "delete_relationship": delete_relationship,
    "create_worldbuilding_entry": create_worldbuilding_entry,
    "update_worldbuilding_entry": update_worldbuilding_entry,
    "delete_worldbuilding_entry": delete_worldbuilding_entry,
    "search_characters": search_characters,
    "search_chapters": search_chapters,
    "search_outline": search_outline,
    "search_outline_tree": search_outline_tree,
    "search_worldbuilding": search_worldbuilding,
    "search_relationships": search_relationships,
    "roleplay_character": roleplay_character,
    "dialogue_battle": dialogue_battle,
    "list_characters": list_characters,
    "list_chapters": list_chapters,
    "list_worldbuilding": list_worldbuilding,
    "rewrite_text": rewrite_text,
    "expand_text": expand_text,
    "continue_text": continue_text,
    "suggest_conflicts": suggest_conflicts,
    "detect_character_changes": detect_character_changes,
    "detect_worldbuilding_conflicts": detect_worldbuilding_conflicts,
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

    handler = TOOL_HANDLERS.get(tool)
    if not handler:
        return {"tool": tool, "status": "skipped", "detail": "未知工具"}
    return await handler(db, project_id, args)
