"""Outline workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import OutlineNode, Project
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ...outline_service import replace_character_links
from ..utils import (
    find_outline_by_title_or_id,
    next_outline_sort_order,
    outline_links_from_names,
    outline_node_payload,
)


async def update_outline_node(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    node_ref = (
        args.get("id")
        or args.get("node_id")
        or args.get("outline_node_id")
        or args.get("current_title")
        or args.get("old_title")
        or args.get("outline_node_title")
        or args.get("title")
    )
    node = find_outline_by_title_or_id(db, project_id, node_ref)
    if not node and args.get("title"):
        node = find_outline_by_title_or_id(db, project_id, args.get("title"))
    if not node:
        return {"tool": "update_outline_node", "status": "skipped", "detail": "未找到当前作品内的大纲节点"}
    character_links = None
    if "character_names" in args:
        character_links = outline_links_from_names(
            db,
            project_id,
            args.get("character_names"),
        )
    if args.get("title"):
        node.title = str(args.get("title")).strip()[:200]
    if "summary" in args:
        node.summary = str(args.get("summary") or "")
    if args.get("status") in {"pending", "in_progress", "completed"}:
        node.status = str(args.get("status"))
    if args.get("node_type") in {"volume", "chapter", "section"}:
        node.node_type = str(args.get("node_type"))
    replace_character_links(db, project_id, node, character_links)
    if "source_chapter_id" in args:
        node.source_chapter_id = str(args.get("source_chapter_id") or "")[:36] or None
    if "actual_summary" in args:
        node.actual_summary = str(args.get("actual_summary") or "") or None
    if "planned_summary" in args:
        node.planned_summary = str(args.get("planned_summary") or "") or None
    if "cataloging_status" in args:
        node.cataloging_status = str(args.get("cataloging_status") or "")[:30] or None
    if "metadata" in args and isinstance(args.get("metadata"), dict):
        node.metadata_json = dict(args.get("metadata"))
    elif any(field in args for field in (
        "scene_number", "purpose", "location", "timeline", "pov_character", "characters",
        "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
    )):
        metadata = dict(node.metadata_json or {})
        for field in (
            "scene_number", "purpose", "location", "timeline", "pov_character", "characters",
            "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
        ):
            if field in args:
                metadata[field] = args[field]
        node.metadata_json = metadata
    node.updated_at = datetime.utcnow()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.OUTLINE,
                source="workspace_tool",
            ),
        )
    return {
        "tool": "update_outline_node",
        "status": "ok",
        "detail": f"已更新大纲：{node.title}",
        "data": outline_node_payload(node),
    }


async def delete_outline_node(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    node_ref = (
        args.get("id")
        or args.get("node_id")
        or args.get("outline_node_id")
        or args.get("title")
    )
    node = find_outline_by_title_or_id(db, project_id, node_ref)
    if not node:
        return {"tool": "delete_outline_node", "status": "skipped", "detail": "未找到大纲节点"}
    title = node.title
    # Cascade-delete children
    children = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.parent_id == node.id)
        .all()
    )
    for child in children:
        db.delete(child)
    db.delete(node)
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        db.flush()
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.OUTLINE,
                entity_id=node.id,
                source="workspace_tool",
            ),
        )
    return {"tool": "delete_outline_node", "status": "ok", "detail": f"已删除大纲：{title}"}
