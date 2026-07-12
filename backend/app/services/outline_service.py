"""Shared outline utilities — node loading, sort context, tree building, and character links."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..core.exceptions import ValidationError
from ..database.models import Character, OutlineNode, OutlineNodeCharacter

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


def load_outline_nodes(db: Session, project_id: str) -> list[OutlineNode]:
    return (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )


def outline_sort_context(nodes: list[OutlineNode]) -> dict:
    node_by_id = {node.id: node for node in nodes}
    children_by_parent: dict[Optional[str], list[OutlineNode]] = {}
    for node in nodes:
        children_by_parent.setdefault(node.parent_id, []).append(node)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda item: (item.sort_order or 0, item.created_at or datetime.min))

    sort_keys: dict[str, tuple[int, ...]] = {}

    def walk(parent_id: Optional[str], prefix: tuple[int, ...]) -> None:
        for index, node in enumerate(children_by_parent.get(parent_id, [])):
            key = (*prefix, index)
            sort_keys[node.id] = key
            walk(node.id, key)

    walk(None, ())

    def path_for(node_id: Optional[str]) -> list[str]:
        if not node_id:
            return []
        path: list[str] = []
        current = node_by_id.get(node_id)
        visited: set[str] = set()
        while current and current.id not in visited:
            visited.add(current.id)
            path.append(current.title)
            current = node_by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))

    return {"nodes": node_by_id, "sort_keys": sort_keys, "path_for": path_for}


def ensure_no_cycle(
    db: Session,
    project_id: str,
    node_id: Optional[str],
    parent_id: Optional[str],
) -> None:
    if not node_id or not parent_id:
        return
    if node_id == parent_id:
        raise ValidationError("节点不能成为自己的父节点")

    current = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == parent_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not current:
        raise ValidationError("父节点不存在")
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


def extract_character_links(
    character_ids: Optional[list[str]],
    characters: Optional[list],
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


def replace_character_links(
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


def node_to_dict(node: OutlineNode) -> dict:
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
        "metadata": node.metadata_json,
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


def build_outline_payload(nodes: list[OutlineNode]) -> dict:
    node_map = {node.id: node_to_dict(node) for node in nodes}
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


def outline_payload(db: Session, project_id: str) -> dict:
    return build_outline_payload(load_outline_nodes(db, project_id))
