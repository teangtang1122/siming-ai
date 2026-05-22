"""Query / search workspace tools — AI uses these to look up project data on demand."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import (
    Chapter,
    Character,
    CharacterRelationship,
    OutlineNode,
    WorldbuildingEntry,
)


async def search_characters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    query = str(args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 10), 30))
    base = db.query(Character).filter(Character.project_id == project_id)
    if query:
        base = base.filter(Character.name.ilike(f"%{query}%"))
    characters = base.order_by(Character.name).limit(limit).all()
    if not characters:
        detail = f"未找到匹配「{query}」的角色" if query else "该项目暂无角色"
        return {"tool": "search_characters", "status": "ok", "detail": detail, "data": []}

    results = []
    for c in characters:
        import json as _json
        abilities = []
        if c.abilities:
            try:
                parsed = _json.loads(c.abilities)
                abilities = parsed if isinstance(parsed, list) else []
            except Exception:
                pass
        results.append({
            "id": c.id,
            "name": c.name,
            "role_type": c.role_type,
            "appearance": c.appearance or "",
            "personality": c.personality or "",
            "background": c.background or "",
            "abilities": abilities,
        })
    return {
        "tool": "search_characters",
        "status": "ok",
        "detail": f"找到 {len(results)} 个角色" + (f"（搜索「{query}」）" if query else ""),
        "data": results,
    }


async def search_chapters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    query = str(args.get("query") or "").strip()
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 5), 20))
    base = db.query(Chapter).filter(Chapter.project_id == project_id)
    if outline_node_id:
        base = base.filter(Chapter.outline_node_id == outline_node_id)
    elif query:
        base = base.filter(Chapter.title.ilike(f"%{query}%"))
    chapters = base.order_by(Chapter.created_at.desc()).limit(limit).all()
    if not chapters:
        detail = "未找到匹配章节"
        return {"tool": "search_chapters", "status": "ok", "detail": detail, "data": []}

    results = []
    for ch in chapters:
        summary_text = ""
        if ch.summary:
            summary_text = ch.summary.summary_text or ""
        results.append({
            "id": ch.id,
            "title": ch.title,
            "outline_node_id": ch.outline_node_id,
            "word_count": ch.word_count or 0,
            "summary": summary_text,
            "content": (ch.content or "")[:8000],
        })
    labels = []
    if query:
        labels.append(f"「{query}」")
    if outline_node_id:
        labels.append(f"大纲节点 {outline_node_id}")
    return {
        "tool": "search_chapters",
        "status": "ok",
        "detail": f"找到 {len(results)} 个章节（{'，'.join(labels)}）" if labels else f"找到 {len(results)} 个章节",
        "data": results,
    }


async def search_outline(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    query = str(args.get("query") or "").strip()
    node_id = str(args.get("node_id") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 10), 60))

    if node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id, OutlineNode.id == node_id
        ).first()
        if not node:
            return {"tool": "search_outline", "status": "ok", "detail": f"未找到大纲节点 {node_id}", "data": []}
        children = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.parent_id == node.id)
            .order_by(OutlineNode.sort_order)
            .all()
        )
        linked = [
            {"id": link.character.id, "name": link.character.name, "role_in_scene": link.role_in_scene}
            for link in (node.linked_characters or [])
            if link.character
        ]
        results = [{
            "id": node.id,
            "parent_id": node.parent_id,
            "node_type": node.node_type,
            "title": node.title,
            "summary": node.summary or "",
            "status": node.status,
            "sort_order": node.sort_order,
            "linked_characters": linked,
            "children": [
                {
                    "id": child.id,
                    "node_type": child.node_type,
                    "title": child.title,
                    "summary": child.summary or "",
                    "status": child.status,
                }
                for child in children
            ],
        }]
        return {
            "tool": "search_outline",
            "status": "ok",
            "detail": f"大纲节点 {node.title}，{len(children)} 个子节点",
            "data": results,
        }

    base = db.query(OutlineNode).filter(OutlineNode.project_id == project_id)
    if query:
        base = base.filter(OutlineNode.title.ilike(f"%{query}%"))
    nodes = base.order_by(OutlineNode.sort_order).limit(limit).all()
    if not nodes:
        detail = f"未找到匹配「{query}」的大纲节点" if query else "该项目暂无大纲"
        return {"tool": "search_outline", "status": "ok", "detail": detail, "data": []}

    results = []
    for node in nodes:
        linked = [
            {"id": link.character.id, "name": link.character.name, "role_in_scene": link.role_in_scene}
            for link in (node.linked_characters or [])
            if link.character
        ]
        results.append({
            "id": node.id,
            "parent_id": node.parent_id,
            "node_type": node.node_type,
            "title": node.title,
            "summary": node.summary or "",
            "status": node.status,
            "sort_order": node.sort_order,
            "linked_characters": linked,
        })
    return {
        "tool": "search_outline",
        "status": "ok",
        "detail": f"找到 {len(results)} 个大纲节点" + (f"（搜索「{query}」）" if query else ""),
        "data": results,
    }


async def search_worldbuilding(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    query = str(args.get("query") or "").strip()
    dimension = str(args.get("dimension") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 10), 30))
    base = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project_id)
    if query:
        base = base.filter(WorldbuildingEntry.title.ilike(f"%{query}%"))
    if dimension:
        base = base.filter(WorldbuildingEntry.dimension == dimension)
    entries = base.order_by(WorldbuildingEntry.sort_order).limit(limit).all()
    if not entries:
        parts = []
        if query:
            parts.append(f"「{query}」")
        if dimension:
            parts.append(f"维度 {dimension}")
        detail = f"未找到匹配（{'，'.join(parts)}）的世界观条目" if parts else "该项目暂无世界观条目"
        return {"tool": "search_worldbuilding", "status": "ok", "detail": detail, "data": []}

    results = [
        {
            "id": e.id,
            "dimension": e.dimension,
            "title": e.title,
            "content": e.content or "",
            "sort_order": e.sort_order,
        }
        for e in entries
    ]
    labels = []
    if query:
        labels.append(f"「{query}」")
    if dimension:
        labels.append(f"维度 {dimension}")
    return {
        "tool": "search_worldbuilding",
        "status": "ok",
        "detail": f"找到 {len(results)} 个世界观条目" + (f"（{'，'.join(labels)}）" if labels else ""),
        "data": results,
    }


async def list_characters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight character catalog — names and IDs only, for quick overview."""
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.name)
        .limit(100)
        .all()
    )
    if not characters:
        return {"tool": "list_characters", "status": "ok", "detail": "该项目暂无角色", "data": []}
    results = [{"id": c.id, "name": c.name, "role_type": c.role_type} for c in characters]
    return {
        "tool": "list_characters",
        "status": "ok",
        "detail": f"共 {len(results)} 个角色",
        "data": results,
    }


def _build_outline_tree(
    db: Session,
    project_id: str,
    nodes: list[OutlineNode],
    parent_id: str | None = None,
) -> list[dict]:
    """Recursively build a lightweight tree — titles and structure only."""
    children = [n for n in nodes if n.parent_id == parent_id]
    children.sort(key=lambda n: n.sort_order)
    return [
        {
            "id": node.id,
            "node_type": node.node_type,
            "title": node.title,
            "children": _build_outline_tree(db, project_id, nodes, node.id),
        }
        for node in children
    ]


async def search_outline_tree(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    root_id = str(args.get("root_id") or "").strip() or None
    all_nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order)
        .all()
    )
    if not all_nodes:
        return {"tool": "search_outline_tree", "status": "ok", "detail": "该项目暂无大纲", "data": []}

    if root_id:
        root = next((n for n in all_nodes if n.id == root_id), None)
        if not root:
            return {"tool": "search_outline_tree", "status": "skipped", "detail": f"未找到大纲节点 {root_id}", "data": []}
        tree = _build_outline_tree(db, project_id, all_nodes, root.id)
        node_count = sum(1 + _count_descendants(n) for n in tree)
        return {
            "tool": "search_outline_tree",
            "status": "ok",
            "detail": f"大纲子树「{root.title}」：{node_count} 个节点",
            "data": tree,
        }

    tree = _build_outline_tree(db, project_id, all_nodes, None)
    return {
        "tool": "search_outline_tree",
        "status": "ok",
        "detail": f"完整大纲树：{len(all_nodes)} 个节点",
        "data": tree,
    }


def _count_descendants(node: dict) -> int:
    return len(node["children"]) + sum(_count_descendants(c) for c in node["children"])


async def list_worldbuilding(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight worldbuilding catalog — id, title, dimension only, for quick overview."""
    entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension, WorldbuildingEntry.sort_order)
        .limit(200)
        .all()
    )
    if not entries:
        return {"tool": "list_worldbuilding", "status": "ok", "detail": "该项目暂无世界观条目", "data": []}
    results = [
        {"id": e.id, "title": e.title, "dimension": e.dimension}
        for e in entries
    ]
    return {
        "tool": "list_worldbuilding",
        "status": "ok",
        "detail": f"共 {len(results)} 个世界观条目",
        "data": results,
    }


async def list_chapters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight chapter catalog — id, title, outline_node_id only, for quick overview."""
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.created_at.desc())
        .limit(500)
        .all()
    )
    if not chapters:
        return {"tool": "list_chapters", "status": "ok", "detail": "该项目暂无章节", "data": []}
    results = [
        {"id": c.id, "title": c.title, "outline_node_id": c.outline_node_id}
        for c in chapters
    ]
    return {
        "tool": "list_chapters",
        "status": "ok",
        "detail": f"共 {len(results)} 个章节",
        "data": results,
    }


async def search_relationships(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    character_id = str(args.get("character_id") or "").strip() or None
    character_name = str(args.get("character_name") or "").strip() or None

    character = None
    if character_id:
        character = db.query(Character).filter(
            Character.project_id == project_id, Character.id == character_id
        ).first()
    if not character and character_name:
        character = db.query(Character).filter(
            Character.project_id == project_id, Character.name == character_name
        ).first()
    if not character:
        label = character_name or character_id or "未知"
        return {"tool": "search_relationships", "status": "skipped", "detail": f"未找到角色：{label}", "data": []}

    rels = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            (CharacterRelationship.character_a_id == character.id)
            | (CharacterRelationship.character_b_id == character.id),
        )
        .limit(50)
        .all()
    )
    character_ids = {character.id}
    for rel in rels:
        character_ids.add(rel.character_a_id)
        character_ids.add(rel.character_b_id)
    name_map = {
        c.id: c.name
        for c in db.query(Character).filter(Character.id.in_(character_ids)).all()
    }

    results = []
    for rel in rels:
        other_id = rel.character_b_id if rel.character_a_id == character.id else rel.character_a_id
        direction = "→" if rel.character_a_id == character.id else "←"
        results.append({
            "character": character.name,
            "direction": direction,
            "target_name": name_map.get(other_id, other_id[:8]),
            "target_id": other_id,
            "relationship_type": rel.relationship_type,
            "description": rel.description or "",
        })

    label = character.name or character_id
    return {
        "tool": "search_relationships",
        "status": "ok",
        "detail": f"「{label}」有 {len(results)} 条关系",
        "data": results,
    }
