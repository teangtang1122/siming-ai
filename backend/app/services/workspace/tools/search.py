"""Query / search workspace tools — AI uses these to look up project data on demand."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....database.models import (
    Chapter,
    Character,
    CharacterRelationship,
    OutlineNode,
    WorldbuildingEntry,
)
from ....database.query_filters import current_worldbuilding_clause
from ....services.hot_cache import get_json, project_cache_key, set_json

_CHARACTER_RANGE_FIELDS = frozenset(
    {
        "appearance",
        "personality",
        "background",
        "abilities",
        "physical_state",
        "mental_state",
        "current_goal",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
    }
)


def _page(values: list[Any], *, cursor: int, limit: int) -> tuple[list[Any], int | None]:
    visible = values[:limit]
    return visible, cursor + len(visible) if len(values) > limit else None


def _text_range(value: Any, *, offset: int, max_chars: int) -> tuple[str, dict[str, Any]]:
    text = str(value or "")
    end = min(len(text), offset + max_chars)
    return text[offset:end], {
        "offset_chars": offset,
        "returned_chars": max(0, end - offset),
        "next_offset_chars": end if end < len(text) else None,
        "has_more": end < len(text),
        "total_chars": len(text),
    }


def _refresh(db: Session, project_id: str) -> None:
    """Compatibility no-op.

    Siming 2.1 treats the database as authoritative. File mirrors are read-only
    context for external/local agents and are never auto-imported during normal
    search because that makes reads slow and can overwrite newer DB data.
    """
    return None


async def search_characters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    query = str(args.get("query") or "").strip()
    if len(query) > 100:
        return {
            "tool": "search_characters",
            "status": "skipped",
            "detail": "角色查询超过100字符，请缩小范围",
            "data": [],
        }
    limit = max(1, min(int(args.get("limit") or 2), 2))
    cursor = max(0, int(args.get("cursor") or 0))
    field_offset = max(0, int(args.get("field_offset_chars") or 0))
    field_chars = max(1, min(int(args.get("field_chars") or 200), 200))
    raw_fields = args.get("fields")
    fields = (
        tuple(dict.fromkeys(str(value).strip() for value in raw_fields if str(value).strip()))
        if isinstance(raw_fields, list)
        else ("appearance", "personality", "background")
    )
    if len(fields) > 3 or any(field not in _CHARACTER_RANGE_FIELDS for field in fields):
        return {
            "tool": "search_characters",
            "status": "skipped",
            "detail": "fields 每次最多选3个声明字段，请分次读取",
            "data": [],
        }
    base = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter(or_(Character.role_type.is_(None), Character.role_type != "merged_alias"))
    )
    if query:
        base = base.filter(Character.name.ilike(f"%{query}%"))
    character_page = (
        base.order_by(Character.name, Character.id).offset(cursor).limit(limit + 1).all()
    )
    characters, next_cursor = _page(character_page, cursor=cursor, limit=limit)
    if not characters:
        detail = f"未找到匹配「{query}」的角色" if query else "该项目暂无角色"
        return {"tool": "search_characters", "status": "ok", "detail": detail, "data": []}

    results: list[dict[str, Any]] = []
    for c in characters:
        selected_fields: dict[str, str] = {}
        ranges: dict[str, dict[str, Any]] = {}
        for field in fields:
            raw_value = getattr(c, field, "")
            if field == "abilities" and raw_value:
                try:
                    raw_value = json.dumps(
                        json.loads(raw_value), ensure_ascii=False, separators=(",", ":")
                    )
                except (TypeError, ValueError):
                    raw_value = str(raw_value)
            visible, range_info = _text_range(
                raw_value,
                offset=field_offset,
                max_chars=field_chars,
            )
            selected_fields[field] = visible
            ranges[field] = range_info
        results.append(
            {
                "id": c.id,
                "name": c.name,
                "role_type": c.role_type,
                "life_status": c.life_status or "",
                "current_location": c.current_location or "",
                "realm_or_level": c.realm_or_level or "",
                "fields": selected_fields,
                "field_ranges": ranges,
            }
        )
    return {
        "tool": "search_characters",
        "status": "ok",
        "detail": f"找到 {len(results)} 个角色" + (f"（搜索「{query}」）" if query else ""),
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }


async def search_chapters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    query = str(args.get("query") or "").strip()
    if len(query) > 200:
        return {
            "tool": "search_chapters",
            "status": "skipped",
            "detail": "章节查询超过200字符，请缩小范围",
            "data": [],
        }
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 2), 2))
    cursor = max(0, int(args.get("cursor") or 0))
    content_offset = max(0, int(args.get("content_offset_chars") or 0))
    content_chars = max(1, min(int(args.get("content_chars") or 400), 400))
    base = db.query(Chapter).filter(Chapter.project_id == project_id)
    if outline_node_id:
        base = base.filter(Chapter.outline_node_id == outline_node_id)
    elif query:
        base = base.filter(Chapter.title.ilike(f"%{query}%"))
    chapter_page = (
        base.order_by(Chapter.created_at.desc(), Chapter.id).offset(cursor).limit(limit + 1).all()
    )
    chapters, next_cursor = _page(chapter_page, cursor=cursor, limit=limit)
    if not chapters:
        detail = "未找到匹配章节"
        return {"tool": "search_chapters", "status": "ok", "detail": detail, "data": []}

    results = []
    for ch in chapters:
        summary_text = ""
        if ch.summary:
            summary_text = ch.summary.summary_text or ""
        content, content_range = _text_range(
            ch.content,
            offset=content_offset,
            max_chars=content_chars,
        )
        results.append(
            {
                "id": ch.id,
                "title": ch.title,
                "outline_node_id": ch.outline_node_id,
                "word_count": ch.word_count or 0,
                "summary": summary_text[:100],
                "summary_truncated": len(summary_text) > 100,
                "content": content,
                "content_range": content_range,
                "quality_score": ch.quality_score,
                "quality_detail": (ch.quality_detail or "")[:100],
                "quality_detail_truncated": len(ch.quality_detail or "") > 100,
                "quality_evaluated_at": ch.quality_evaluated_at.isoformat()
                if ch.quality_evaluated_at
                else None,
            }
        )
    labels = []
    if query:
        labels.append(f"「{query}」")
    if outline_node_id:
        labels.append(f"大纲节点 {outline_node_id}")
    return {
        "tool": "search_chapters",
        "status": "ok",
        "detail": f"找到 {len(results)} 个章节（{'，'.join(labels)}）"
        if labels
        else f"找到 {len(results)} 个章节",
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }


def _outline_linked_page(
    node: OutlineNode,
    *,
    cursor: int,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = [link for link in (node.linked_characters or []) if link.character]
    visible, next_cursor = _page(source[cursor:], cursor=cursor, limit=limit)
    return (
        [
            {
                "id": link.character.id,
                "name": link.character.name,
                "role_in_scene": link.role_in_scene,
            }
            for link in visible
        ],
        {
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        },
    )


def _outline_search_item(
    node: OutlineNode,
    *,
    summary_offset: int,
    summary_chars: int,
    linked_cursor: int,
    linked_limit: int,
    children: list[OutlineNode] | None = None,
) -> dict[str, Any]:
    summary, summary_range = _text_range(
        node.summary,
        offset=summary_offset,
        max_chars=summary_chars,
    )
    actual_summary, actual_range = _text_range(
        node.actual_summary,
        offset=summary_offset,
        max_chars=summary_chars,
    )
    planned_summary, planned_range = _text_range(
        node.planned_summary,
        offset=summary_offset,
        max_chars=summary_chars,
    )
    linked, linked_page = _outline_linked_page(
        node,
        cursor=linked_cursor,
        limit=linked_limit,
    )
    result = {
        "id": node.id,
        "parent_id": node.parent_id,
        "node_type": node.node_type,
        "title": node.title,
        "summary": summary,
        "summary_range": summary_range,
        "status": node.status,
        "sort_order": node.sort_order,
        "source_chapter_id": node.source_chapter_id,
        "actual_summary": actual_summary,
        "actual_summary_range": actual_range,
        "planned_summary": planned_summary,
        "planned_summary_range": planned_range,
        "cataloging_status": node.cataloging_status,
        "linked_characters": linked,
        "linked_page": linked_page,
    }
    if children is not None:
        result["children"] = [
            {
                "id": child.id,
                "node_type": child.node_type,
                "title": child.title,
                "summary": _text_range(
                    child.summary,
                    offset=summary_offset,
                    max_chars=summary_chars,
                )[0],
                "status": child.status,
            }
            for child in children
        ]
    return result


async def search_outline(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    query = str(args.get("query") or "").strip()
    if len(query) > 200:
        return {
            "tool": "search_outline",
            "status": "skipped",
            "detail": "大纲查询超过200字符，请缩小范围",
            "data": [],
        }
    node_id = str(args.get("node_id") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 2), 2))
    cursor = max(0, int(args.get("cursor") or 0))
    summary_offset = max(0, int(args.get("summary_offset_chars") or 0))
    summary_chars = max(1, min(int(args.get("summary_chars") or 100), 100))
    linked_cursor = max(0, int(args.get("linked_cursor") or 0))
    linked_limit = max(1, min(int(args.get("linked_limit") or 2), 2))

    def page_payload(returned_items: int, total_items: int, next_cursor: int | None) -> dict:
        return {
            "cursor": cursor,
            "limit": limit,
            "returned_items": returned_items,
            "total_items": total_items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        }

    def next_arguments(next_cursor: int | None) -> dict[str, Any] | None:
        if next_cursor is None:
            return None
        result: dict[str, Any] = {
            "cursor": next_cursor,
            "limit": limit,
            "summary_offset_chars": summary_offset,
            "summary_chars": summary_chars,
            "linked_cursor": linked_cursor,
            "linked_limit": linked_limit,
        }
        if node_id:
            result["node_id"] = node_id
        elif query:
            result["query"] = query
        return result

    if node_id:
        node = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.id == node_id)
            .first()
        )
        if not node:
            return {
                "tool": "search_outline",
                "status": "ok",
                "detail": f"未找到大纲节点 {node_id}",
                "data": [],
            }
        children_query = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.parent_id == node.id,
        )
        total_items = children_query.count()
        children = (
            children_query.order_by(OutlineNode.sort_order, OutlineNode.id)
            .offset(cursor)
            .limit(limit + 1)
            .all()
        )
        children, next_cursor = _page(children, cursor=cursor, limit=limit)
        results = [
            _outline_search_item(
                node,
                summary_offset=summary_offset,
                summary_chars=summary_chars,
                linked_cursor=linked_cursor,
                linked_limit=linked_limit,
                children=children,
            )
        ]
        detail = (
            f"大纲节点 {node.title}：子节点共 {total_items} 个，"
            f"本页返回 {len(children)} 个"
        )
        if next_cursor is not None:
            detail += f"；尚有未返回子节点，请用 next_cursor={next_cursor} 继续"
        result = {
            "tool": "search_outline",
            "status": "ok",
            "detail": detail,
            "data": results,
            "page": page_payload(len(children), total_items, next_cursor),
        }
        continuation = next_arguments(next_cursor)
        if continuation is not None:
            result["next_arguments"] = continuation
        return result

    base = db.query(OutlineNode).filter(OutlineNode.project_id == project_id)
    if query:
        base = base.filter(OutlineNode.title.ilike(f"%{query}%"))
    total_items = base.count()
    node_page = (
        base.order_by(OutlineNode.sort_order, OutlineNode.id).offset(cursor).limit(limit + 1).all()
    )
    nodes, next_cursor = _page(node_page, cursor=cursor, limit=limit)
    if not nodes:
        if total_items == 0:
            detail = f"未找到匹配「{query}」的大纲节点" if query else "该项目暂无大纲"
        else:
            detail = f"匹配大纲节点共 {total_items} 个，cursor={cursor} 后本页无数据"
        return {
            "tool": "search_outline",
            "status": "ok",
            "detail": detail,
            "data": [],
            "page": page_payload(0, total_items, None),
        }

    results = [
        _outline_search_item(
            node,
            summary_offset=summary_offset,
            summary_chars=summary_chars,
            linked_cursor=linked_cursor,
            linked_limit=linked_limit,
        )
        for node in nodes
    ]
    detail = f"匹配大纲节点共 {total_items} 个，本页返回 {len(results)} 个"
    if query:
        detail += f"（搜索「{query}」）"
    if next_cursor is not None:
        detail += f"；尚有未返回节点，请用 next_cursor={next_cursor} 继续"
    result = {
        "tool": "search_outline",
        "status": "ok",
        "detail": detail,
        "data": results,
        "page": page_payload(len(results), total_items, next_cursor),
    }
    continuation = next_arguments(next_cursor)
    if continuation is not None:
        result["next_arguments"] = continuation
    return result


async def search_worldbuilding(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    query = str(args.get("query") or "").strip()
    if len(query) > 200:
        return {
            "tool": "search_worldbuilding",
            "status": "skipped",
            "detail": "世界观查询超过200字符，请缩小范围",
            "data": [],
        }
    dimension = str(args.get("dimension") or "").strip() or None
    limit = max(1, min(int(args.get("limit") or 2), 2))
    cursor = max(0, int(args.get("cursor") or 0))
    content_offset = max(0, int(args.get("content_offset_chars") or 0))
    content_chars = max(1, min(int(args.get("content_chars") or 400), 400))
    base = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
        current_worldbuilding_clause(WorldbuildingEntry.status),
    )
    if query:
        base = base.filter(WorldbuildingEntry.title.ilike(f"%{query}%"))
    if dimension:
        base = base.filter(WorldbuildingEntry.dimension == dimension)
    entry_page = (
        base.order_by(WorldbuildingEntry.sort_order, WorldbuildingEntry.id)
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    entries, next_cursor = _page(entry_page, cursor=cursor, limit=limit)
    if not entries:
        parts = []
        if query:
            parts.append(f"「{query}」")
        if dimension:
            parts.append(f"维度 {dimension}")
        detail = (
            f"未找到匹配（{'，'.join(parts)}）的世界观条目" if parts else "该项目暂无世界观条目"
        )
        return {"tool": "search_worldbuilding", "status": "ok", "detail": detail, "data": []}

    results = []
    for entry in entries:
        content, content_range = _text_range(
            entry.content,
            offset=content_offset,
            max_chars=content_chars,
        )
        results.append(
            {
                "id": entry.id,
                "dimension": entry.dimension,
                "title": entry.title,
                "content": content,
                "content_range": content_range,
                "sort_order": entry.sort_order,
                "status": entry.status,
                "confidence": entry.confidence,
                "first_seen_chapter_id": entry.first_seen_chapter_id,
                "last_updated_chapter_id": entry.last_updated_chapter_id,
            }
        )
    labels = []
    if query:
        labels.append(f"「{query}」")
    if dimension:
        labels.append(f"维度 {dimension}")
    return {
        "tool": "search_worldbuilding",
        "status": "ok",
        "detail": f"找到 {len(results)} 个世界观条目"
        + (f"（{'，'.join(labels)}）" if labels else ""),
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }


async def list_characters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight character catalog — names and IDs only, for quick overview."""
    _refresh(db, project_id)
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 10), 10))
    cache_key = project_cache_key(project_id, "workspace:list_characters", f"{cursor}:{limit}")
    cached = get_json(cache_key)
    if cached is not None:
        return cached
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter(or_(Character.role_type.is_(None), Character.role_type != "merged_alias"))
        .order_by(Character.name, Character.id)
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    characters, next_cursor = _page(characters, cursor=cursor, limit=limit)
    if not characters:
        result = {"tool": "list_characters", "status": "ok", "detail": "该项目暂无角色", "data": []}
        set_json(cache_key, result)
        return result
    results = [{"id": c.id, "name": c.name, "role_type": c.role_type} for c in characters]
    result = {
        "tool": "list_characters",
        "status": "ok",
        "detail": f"共 {len(results)} 个角色",
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }
    set_json(cache_key, result)
    return result


def _flatten_outline_nodes(
    nodes: list[OutlineNode],
    parent_id: str | None,
    *,
    depth: int = 0,
) -> list[dict[str, Any]]:
    """Build a stable preorder page source without an unbounded nested payload."""

    children = sorted(
        (node for node in nodes if node.parent_id == parent_id),
        key=lambda node: (node.sort_order, node.id),
    )
    flattened: list[dict[str, Any]] = []
    for node in children:
        flattened.append(
            {
                "id": node.id,
                "parent_id": node.parent_id,
                "node_type": node.node_type,
                "title": node.title,
                "depth": depth,
                "sort_order": node.sort_order,
            }
        )
        flattened.extend(_flatten_outline_nodes(nodes, node.id, depth=depth + 1))
    return flattened


async def search_outline_tree(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    root_id = str(args.get("root_id") or "").strip() or None
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 10), 10))
    cache_key = project_cache_key(
        project_id,
        "workspace:outline_tree",
        f"{root_id or 'root'}:{cursor}:{limit}",
    )
    cached = get_json(cache_key)
    if cached is not None:
        return cached
    all_nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order)
        .all()
    )
    if not all_nodes:
        result = {
            "tool": "search_outline_tree",
            "status": "ok",
            "detail": "该项目暂无大纲",
            "data": [],
        }
        set_json(cache_key, result)
        return result

    if root_id:
        root = next((n for n in all_nodes if n.id == root_id), None)
        if not root:
            return {
                "tool": "search_outline_tree",
                "status": "skipped",
                "detail": f"未找到大纲节点 {root_id}",
                "data": [],
            }
        flattened = _flatten_outline_nodes(all_nodes, root.id, depth=1)
        page, next_cursor = _page(flattened[cursor:], cursor=cursor, limit=limit)
        result = {
            "tool": "search_outline_tree",
            "status": "ok",
            "detail": f"大纲子树「{root.title}」：{len(flattened)} 个节点",
            "data": page,
            "page": {
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
                "total_items": len(flattened),
            },
        }
        set_json(cache_key, result)
        return result

    flattened = _flatten_outline_nodes(all_nodes, None)
    page, next_cursor = _page(flattened[cursor:], cursor=cursor, limit=limit)
    result = {
        "tool": "search_outline_tree",
        "status": "ok",
        "detail": f"完整大纲树：{len(all_nodes)} 个节点",
        "data": page,
        "page": {
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "total_items": len(flattened),
        },
    }
    set_json(cache_key, result)
    return result


async def list_worldbuilding(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight worldbuilding catalog — id, title, dimension only, for quick overview."""
    _refresh(db, project_id)
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 10), 10))
    cache_key = project_cache_key(project_id, "workspace:list_worldbuilding", f"{cursor}:{limit}")
    cached = get_json(cache_key)
    if cached is not None:
        return cached
    entries = (
        db.query(WorldbuildingEntry)
        .filter(
            WorldbuildingEntry.project_id == project_id,
            current_worldbuilding_clause(WorldbuildingEntry.status),
        )
        .order_by(
            WorldbuildingEntry.dimension, WorldbuildingEntry.sort_order, WorldbuildingEntry.id
        )
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    entries, next_cursor = _page(entries, cursor=cursor, limit=limit)
    if not entries:
        result = {
            "tool": "list_worldbuilding",
            "status": "ok",
            "detail": "该项目暂无世界观条目",
            "data": [],
        }
        set_json(cache_key, result)
        return result
    results = [{"id": e.id, "title": e.title, "dimension": e.dimension} for e in entries]
    result = {
        "tool": "list_worldbuilding",
        "status": "ok",
        "detail": f"共 {len(results)} 个世界观条目",
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }
    set_json(cache_key, result)
    return result


async def list_chapters(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Lightweight chapter catalog — id, title, outline_node_id only, for quick overview."""
    _refresh(db, project_id)
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 10), 10))
    cache_key = project_cache_key(project_id, "workspace:list_chapters", f"{cursor}:{limit}")
    cached = get_json(cache_key)
    if cached is not None:
        return cached
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.created_at.desc(), Chapter.id)
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    chapters, next_cursor = _page(chapters, cursor=cursor, limit=limit)
    if not chapters:
        result = {"tool": "list_chapters", "status": "ok", "detail": "该项目暂无章节", "data": []}
        set_json(cache_key, result)
        return result
    results = [
        {"id": c.id, "title": c.title, "outline_node_id": c.outline_node_id} for c in chapters
    ]
    result = {
        "tool": "list_chapters",
        "status": "ok",
        "detail": f"共 {len(results)} 个章节",
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }
    set_json(cache_key, result)
    return result


async def search_relationships(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    _refresh(db, project_id)
    character_id = str(args.get("character_id") or "").strip() or None
    character_name = str(args.get("character_name") or "").strip() or None
    if character_name and len(character_name) > 100:
        return {
            "tool": "search_relationships",
            "status": "skipped",
            "detail": "角色名超过100字符，请缩小范围",
            "data": [],
        }
    cursor = max(0, int(args.get("cursor") or 0))
    limit = max(1, min(int(args.get("limit") or 5), 5))
    description_offset = max(0, int(args.get("description_offset_chars") or 0))
    description_chars = max(1, min(int(args.get("description_chars") or 100), 100))

    character = None
    if character_id:
        character = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.id == character_id)
            .first()
        )
    if not character and character_name:
        character = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name == character_name)
            .first()
        )
    if not character:
        label = character_name or character_id or "未知"
        return {
            "tool": "search_relationships",
            "status": "skipped",
            "detail": f"未找到角色：{label}",
            "data": [],
        }

    relationship_page = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            (CharacterRelationship.character_a_id == character.id)
            | (CharacterRelationship.character_b_id == character.id),
        )
        .order_by(CharacterRelationship.id)
        .offset(cursor)
        .limit(limit + 1)
        .all()
    )
    rels, next_cursor = _page(relationship_page, cursor=cursor, limit=limit)
    character_ids = {character.id}
    for rel in rels:
        character_ids.add(rel.character_a_id)
        character_ids.add(rel.character_b_id)
    name_map = {
        c.id: c.name for c in db.query(Character).filter(Character.id.in_(character_ids)).all()
    }

    results = []
    for rel in rels:
        other_id = rel.character_b_id if rel.character_a_id == character.id else rel.character_a_id
        direction = "→" if rel.character_a_id == character.id else "←"
        description, description_range = _text_range(
            rel.description,
            offset=description_offset,
            max_chars=description_chars,
        )
        results.append(
            {
                "id": rel.id,
                "character": character.name,
                "direction": direction,
                "target_name": name_map.get(other_id, other_id[:8]),
                "target_id": other_id,
                "relationship_type": rel.relationship_type,
                "description": description,
                "description_range": description_range,
            }
        )

    label = character.name or character_id
    return {
        "tool": "search_relationships",
        "status": "ok",
        "detail": f"「{label}」有 {len(results)} 条关系",
        "data": results,
        "page": {"cursor": cursor, "next_cursor": next_cursor, "has_more": next_cursor is not None},
    }
