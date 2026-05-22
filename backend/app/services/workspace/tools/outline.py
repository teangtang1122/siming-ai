"""Outline workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import OutlineNode
from ..types import WorkspaceActionDependencies
from ..utils import (
    find_outline_by_title_or_id,
    next_outline_sort_order,
    outline_node_payload,
    replace_outline_links_by_names,
)


async def create_outline_node(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
) -> dict:
    parent_id = str(args.get("parent_id") or "").strip() or None
    parent_warning = ""
    if parent_id:
        parent = find_outline_by_title_or_id(db, project_id, parent_id)
        if parent:
            parent_id = parent.id
        else:
            parent_id = None
            parent_warning = "；未找到当前作品内的父级大纲，已作为根节点创建"
    node_type = str(args.get("node_type") or "chapter")
    if node_type not in {"volume", "chapter", "section"}:
        node_type = "chapter"
    title = str(args.get("title") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not title:
        return {"tool": "create_outline_node", "status": "skipped", "detail": "标题为空"}
    node = OutlineNode(
        project_id=project_id,
        parent_id=parent_id,
        node_type=node_type,
        title=title[:200],
        summary=summary,
        status=str(args.get("status") or "pending"),
        sort_order=int(
            args.get("sort_order")
            if args.get("sort_order") is not None
            else next_outline_sort_order(db, project_id, parent_id)
        ),
    )
    db.add(node)
    db.flush()
    replace_outline_links_by_names(db, project_id, node, args.get("character_names"))
    return {
        "tool": "create_outline_node",
        "status": "ok",
        "detail": f"已创建大纲：{node.title}{parent_warning}",
        "data": outline_node_payload(node),
    }


async def update_outline_node(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
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
    if args.get("title"):
        node.title = str(args.get("title")).strip()[:200]
    if "summary" in args:
        node.summary = str(args.get("summary") or "")
    if args.get("status") in {"pending", "in_progress", "completed"}:
        node.status = str(args.get("status"))
    if args.get("node_type") in {"volume", "chapter", "section"}:
        node.node_type = str(args.get("node_type"))
    if "character_names" in args:
        replace_outline_links_by_names(db, project_id, node, args.get("character_names"))
    node.updated_at = datetime.utcnow()
    return {
        "tool": "update_outline_node",
        "status": "ok",
        "detail": f"已更新大纲：{node.title}",
        "data": outline_node_payload(node),
    }

