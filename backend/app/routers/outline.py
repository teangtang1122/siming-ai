"""Outline tree CRUD, reorder, and AI suggestion endpoints."""
import json
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import (
    Character,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
)
from ..database.session import get_db
from ..schemas.outline import (
    OutlineCharacterLinkInput,
    OutlineNodeCreate,
    OutlineNodeUpdate,
    OutlineReorderItem,
    OutlineReorderRequest,
)

router = APIRouter(tags=["outline"])


NODE_TYPE_LABELS = {
    "volume": "卷",
    "chapter": "章",
    "section": "节",
}

STATUS_LABELS = {
    "pending": "待规划",
    "in_progress": "进行中",
    "completed": "已完成",
}


def _get_node_or_404(db: Session, project_id: str, node_id: str) -> OutlineNode:
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise NotFoundError("大纲节点不存在")
    return node


def _get_parent_or_404(db: Session, project_id: str, parent_id: Optional[str]) -> Optional[OutlineNode]:
    if not parent_id:
        return None
    return _get_node_or_404(db, project_id, parent_id)


def _ensure_no_cycle(
    db: Session,
    project_id: str,
    node_id: Optional[str],
    parent_id: Optional[str],
) -> None:
    if not node_id or not parent_id:
        return
    if node_id == parent_id:
        raise ValidationError("节点不能成为自己的父节点")

    current = _get_parent_or_404(db, project_id, parent_id)
    visited: set[str] = set()
    while current:
        if current.id == node_id:
            raise ValidationError("不能把节点移动到自己的子节点下")
        if current.id in visited:
            raise ValidationError("检测到循环大纲结构")
        visited.add(current.id)
        current = (
            db.query(OutlineNode)
            .filter(OutlineNode.id == current.parent_id, OutlineNode.project_id == project_id)
            .first()
            if current.parent_id
            else None
        )


def _extract_character_links(
    character_ids: Optional[list[str]],
    characters: Optional[list[OutlineCharacterLinkInput]],
) -> Optional[list[tuple[str, Optional[str]]]]:
    if characters is not None:
        raw_links = [(item.character_id, item.role_in_scene) for item in characters]
    elif character_ids is not None:
        raw_links = [(character_id, None) for character_id in character_ids]
    else:
        return None

    links: list[tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for character_id, role_in_scene in raw_links:
        if character_id in seen:
            continue
        seen.add(character_id)
        links.append((character_id, role_in_scene))
    return links


def _replace_character_links(
    db: Session,
    project_id: str,
    node: OutlineNode,
    links: Optional[list[tuple[str, Optional[str]]]],
) -> None:
    if links is None:
        return

    character_ids = [character_id for character_id, _role in links]
    if character_ids:
        count = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.id.in_(character_ids))
            .count()
        )
        if count != len(character_ids):
            raise ValidationError("关联角色必须属于当前作品")

    node.linked_characters.clear()
    db.flush()
    for character_id, role_in_scene in links:
        node.linked_characters.append(
            OutlineNodeCharacter(character_id=character_id, role_in_scene=role_in_scene)
        )


def _node_to_dict(node: OutlineNode) -> dict:
    return {
        "id": node.id,
        "project_id": node.project_id,
        "parent_id": node.parent_id,
        "node_type": node.node_type,
        "node_type_label": NODE_TYPE_LABELS.get(node.node_type, node.node_type),
        "title": node.title,
        "summary": node.summary,
        "status": node.status,
        "status_label": STATUS_LABELS.get(node.status, node.status),
        "sort_order": node.sort_order,
        "linked_characters": [
            {
                "id": link.character.id,
                "name": link.character.name,
                "role_type": link.character.role_type,
                "role_in_scene": link.role_in_scene,
            }
            for link in sorted(
                node.linked_characters,
                key=lambda item: item.created_at,
            )
            if link.character is not None
        ],
        "children": [],
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def _load_outline_nodes(db: Session, project_id: str) -> list[OutlineNode]:
    return (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )


def _build_outline_payload(nodes: list[OutlineNode]) -> dict:
    node_map = {node.id: _node_to_dict(node) for node in nodes}
    roots: list[dict] = []
    for node in nodes:
        item = node_map[node.id]
        parent = node_map.get(node.parent_id) if node.parent_id else None
        if parent is None:
            roots.append(item)
        else:
            parent["children"].append(item)

    return {
        "items": roots,
        "flat": [node_map[node.id] for node in nodes],
        "total": len(nodes),
    }


def _outline_payload(db: Session, project_id: str) -> dict:
    return _build_outline_payload(_load_outline_nodes(db, project_id))


def _normalize_reorder_items(payload: OutlineReorderRequest) -> list[OutlineReorderItem]:
    if payload.sort_order is not None:
        return [
            OutlineReorderItem(id=node_id, parent_id=payload.parent_id, sort_order=index)
            for index, node_id in enumerate(payload.sort_order)
        ]
    return payload.items


@router.get("/projects/{project_id}/outline")
def get_outline(project_id: str, db: Session = Depends(get_db)):
    """Get the full outline tree for a project."""
    get_project_or_404(db, project_id)
    return ApiResponse.success(data=_outline_payload(db, project_id))


@router.post("/projects/{project_id}/outline")
def create_outline_node(
    project_id: str,
    payload: OutlineNodeCreate,
    db: Session = Depends(get_db),
):
    """Create an outline node."""
    get_project_or_404(db, project_id)
    _get_parent_or_404(db, project_id, payload.parent_id)

    node = OutlineNode(
        project_id=project_id,
        parent_id=payload.parent_id,
        node_type=payload.node_type,
        title=payload.title,
        summary=payload.summary,
        status=payload.status,
        sort_order=payload.sort_order,
    )
    db.add(node)
    db.flush()
    links = _extract_character_links(payload.character_ids, payload.characters)
    _replace_character_links(db, project_id, node, links or [])
    db.commit()
    db.refresh(node)
    return ApiResponse.success(data=_node_to_dict(node), message="大纲节点已创建")


@router.put("/projects/{project_id}/outline/reorder")
def reorder_outline(
    project_id: str,
    payload: OutlineReorderRequest,
    db: Session = Depends(get_db),
):
    """Reorder outline nodes and optionally move them to a new parent."""
    get_project_or_404(db, project_id)
    items = _normalize_reorder_items(payload)
    if not items:
        raise ValidationError("未提供排序数据")

    touched_ids = [item.id for item in items]
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id.in_(touched_ids))
        .all()
    )
    node_by_id = {node.id: node for node in nodes}
    if len(node_by_id) != len(set(touched_ids)):
        raise ValidationError("排序节点必须属于当前作品")

    for item in items:
        node = node_by_id[item.id]
        _get_parent_or_404(db, project_id, item.parent_id)
        _ensure_no_cycle(db, project_id, node.id, item.parent_id)
        node.parent_id = item.parent_id
        node.sort_order = item.sort_order

    db.commit()
    return ApiResponse.success(data=_outline_payload(db, project_id), message="大纲排序已更新")



@router.put("/projects/{project_id}/outline/{node_id}")
def update_outline_node(
    project_id: str,
    node_id: str,
    payload: OutlineNodeUpdate,
    db: Session = Depends(get_db),
):
    """Update an outline node and its linked characters."""
    get_project_or_404(db, project_id)
    node = _get_node_or_404(db, project_id, node_id)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    links = _extract_character_links(update_data.pop("character_ids", None), update_data.pop("characters", None))
    if "parent_id" in update_data:
        _get_parent_or_404(db, project_id, update_data["parent_id"])
        _ensure_no_cycle(db, project_id, node.id, update_data["parent_id"])

    for field, value in update_data.items():
        setattr(node, field, value)
    _replace_character_links(db, project_id, node, links)

    db.commit()
    db.refresh(node)
    return ApiResponse.success(data=_node_to_dict(node), message="大纲节点已更新")


@router.delete("/projects/{project_id}/outline/{node_id}")
def delete_outline_node(project_id: str, node_id: str, db: Session = Depends(get_db)):
    """Delete an outline node and its descendants."""
    get_project_or_404(db, project_id)
    node = _get_node_or_404(db, project_id, node_id)
    db.delete(node)
    db.commit()
    return ApiResponse.success(message="大纲节点已删除")
