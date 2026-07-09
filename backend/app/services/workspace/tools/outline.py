"""Outline workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import OutlineNode, Project
from ....services.content_store import sync_outline_to_file
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
) -> dict:
    parent_id = str(args.get("parent_id") or "").strip() or None
    parent_title = str(args.get("parent_title") or "").strip()
    if not parent_id and parent_title:
        parent = find_outline_by_title_or_id(db, project_id, parent_title)
        if parent:
            parent_id = parent.id
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
    existing = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == project_id,
            OutlineNode.parent_id == parent_id,
            OutlineNode.title == title[:200],
        )
        .first()
    )
    if existing:
        return {
            "tool": "create_outline_node",
            "status": "ok",
            "detail": f"大纲已存在：{existing.title}",
            "data": outline_node_payload(existing),
        }

    from ..run_recovery import generate_idempotency_key, check_idempotency
    _idem_key = generate_idempotency_key(db, "create_outline_node", project_id, args)
    if _idem_key:
        _existing = check_idempotency(db, project_id, _idem_key)
        if _existing:
            return _existing
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
        source_chapter_id=str(args.get("source_chapter_id") or "")[:36] or None,
        actual_summary=str(args.get("actual_summary") or "") or None,
        planned_summary=str(args.get("planned_summary") or "") or None,
        cataloging_status=str(args.get("cataloging_status") or "")[:30] or None,
    )
    db.add(node)
    db.flush()
    character_names = args.get("character_names")
    if character_names is None:
        character_names = args.get("related_characters")
    replace_outline_links_by_names(db, project_id, node, character_names)
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_outline_to_file(db, project)
        db.flush()
    return {
        "tool": "create_outline_node",
        "status": "ok",
        "detail": f"已创建大纲：{node.title}{parent_warning}",
        "data": outline_node_payload(node),
    }


async def create_outline_nodes(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    nodes = args.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return {"tool": "create_outline_nodes", "status": "skipped", "detail": "没有可创建的大纲节点", "data": {"nodes": []}}

    parent_id = str(args.get("parent_id") or "").strip()
    created_title_to_id: dict[str, str] = {}
    created: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(nodes[:8], start=1):
        if not isinstance(item, dict):
            skipped.append(f"第 {index} 个节点格式无效")
            continue
        node_args = dict(item)
        if parent_id and not node_args.get("parent_id"):
            node_args["parent_id"] = parent_id
        parent_title = str(node_args.get("parent_title") or "").strip()
        if parent_title and not node_args.get("parent_id") and parent_title in created_title_to_id:
            node_args["parent_id"] = created_title_to_id[parent_title]
        result = await create_outline_node(db, project_id, node_args)
        status = str(result.get("status") or "")
        if status == "ok":
            data = result.get("data")
            if isinstance(data, dict):
                created.append(data)
                title = str(data.get("title") or "").strip()
                node_id = str(data.get("id") or "").strip()
                if title and node_id:
                    created_title_to_id[title] = node_id
        elif status == "error":
            errors.append(str(result.get("detail") or f"第 {index} 个节点创建失败"))
        else:
            skipped.append(str(result.get("detail") or f"第 {index} 个节点已跳过"))

    if errors:
        return {
            "tool": "create_outline_nodes",
            "status": "error",
            "detail": "；".join(errors[:3]),
            "data": {"nodes": created, "skipped": skipped},
        }
    if not created:
        return {
            "tool": "create_outline_nodes",
            "status": "skipped",
            "detail": "未创建新的大纲节点" + (f"：{'；'.join(skipped[:3])}" if skipped else ""),
            "data": {"nodes": [], "skipped": skipped},
        }
    detail = f"已创建 {len(created)} 个大纲节点"
    if skipped:
        detail += f"，跳过 {len(skipped)} 个"
    return {
        "tool": "create_outline_nodes",
        "status": "ok",
        "detail": detail,
        "data": {"nodes": created, "skipped": skipped},
    }


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
    if "source_chapter_id" in args:
        node.source_chapter_id = str(args.get("source_chapter_id") or "")[:36] or None
    if "actual_summary" in args:
        node.actual_summary = str(args.get("actual_summary") or "") or None
    if "planned_summary" in args:
        node.planned_summary = str(args.get("planned_summary") or "") or None
    if "cataloging_status" in args:
        node.cataloging_status = str(args.get("cataloging_status") or "")[:30] or None
    node.updated_at = datetime.utcnow()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_outline_to_file(db, project)
        db.flush()
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
        sync_outline_to_file(db, project)
    db.flush()
    return {"tool": "delete_outline_node", "status": "ok", "detail": f"已删除大纲：{title}"}
