"""Apply an author-confirmed outline draft inside its owning transaction."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import OutlineNode, Project
from ...modules.story.application.content_sync import queue_content_sync
from ...modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ...modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from ..outline_service import replace_character_links
from .utils import (
    find_outline_by_title_or_id,
    next_outline_sort_order,
    outline_links_from_names,
    outline_node_payload,
)


async def _create_node(
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
        return {"tool": "confirm_outline_draft", "status": "error", "detail": "大纲节点类型无效"}
    title = str(args.get("title") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not title:
        return {"tool": "confirm_outline_draft", "status": "skipped", "detail": "标题为空"}
    metadata = dict(args.get("metadata")) if isinstance(args.get("metadata"), dict) else {}
    for field in (
        "scene_number", "purpose", "location", "timeline", "pov_character", "characters",
        "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
    ):
        if field in args and field not in metadata:
            metadata[field] = args[field]
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
            "tool": "confirm_outline_draft",
            "status": "ok",
            "detail": f"大纲已存在：{existing.title}",
            "data": outline_node_payload(existing),
        }

    character_names = args.get("character_names")
    if character_names is None:
        character_names = args.get("related_characters")
    character_links = (
        outline_links_from_names(db, project_id, character_names)
        if character_names is not None
        else []
    )
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
        metadata_json=metadata or None,
    )
    db.add(node)
    db.flush()
    replace_character_links(db, project_id, node, character_links)
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.OUTLINE,
                entity_id=node.id,
                source="outline_draft_confirmation",
            ),
        )
    return {
        "tool": "confirm_outline_draft",
        "status": "ok",
        "detail": f"已创建大纲：{node.title}{parent_warning}",
        "data": outline_node_payload(node),
    }


async def apply_confirmed_outline_nodes(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    nodes = args.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return {"tool": "confirm_outline_draft", "status": "skipped", "detail": "没有可创建的大纲节点", "data": {"nodes": []}}

    if len(nodes) > OUTLINE_PROPOSAL_MAX_NODES:
        return {
            "tool": "confirm_outline_draft",
            "status": "error",
            "detail": (
                f"单次最多创建 {OUTLINE_PROPOSAL_MAX_NODES} 个大纲节点；"
                "本次未写入任何节点"
            ),
            "data": {"nodes": [], "skipped": []},
        }
    parent_id = str(args.get("parent_id") or "").strip()
    created_title_to_id: dict[str, str] = {}
    created: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(nodes, start=1):
        if not isinstance(item, dict):
            skipped.append(f"第 {index} 个节点格式无效")
            continue
        node_args = dict(item)
        if parent_id and not node_args.get("parent_id"):
            node_args["parent_id"] = parent_id
        parent_title = str(node_args.get("parent_title") or "").strip()
        if parent_title and not node_args.get("parent_id") and parent_title in created_title_to_id:
            node_args["parent_id"] = created_title_to_id[parent_title]
        result = await _create_node(db, project_id, node_args)
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
            "tool": "confirm_outline_draft",
            "status": "error",
            "detail": "；".join(errors[:3]),
            "data": {"nodes": created, "skipped": skipped},
        }
    if not created:
        return {
            "tool": "confirm_outline_draft",
            "status": "skipped",
            "detail": "未创建新的大纲节点" + (f"：{'；'.join(skipped[:3])}" if skipped else ""),
            "data": {"nodes": [], "skipped": skipped},
        }
    detail = f"已创建 {len(created)} 个大纲节点"
    if skipped:
        detail += f"，跳过 {len(skipped)} 个"
    return {
        "tool": "confirm_outline_draft",
        "status": "ok",
        "detail": detail,
        "data": {"nodes": created, "skipped": skipped},
    }
