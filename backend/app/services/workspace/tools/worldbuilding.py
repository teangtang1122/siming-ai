"""Worldbuilding workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Project, WorldbuildingEntry
from ....services.content_store import delete_project_file, sync_worldbuilding_to_file
from ..utils import (
    WORLD_DIMENSIONS,
    find_worldbuilding_by_title_or_id,
    next_worldbuilding_sort_order,
    worldbuilding_payload,
)


async def create_worldbuilding_entry(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    dimension = str(args.get("dimension") or "culture").strip()
    if dimension not in WORLD_DIMENSIONS:
        dimension = "culture"
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    if not title or not content:
        return {"tool": "create_worldbuilding_entry", "status": "skipped", "detail": "世界观标题或内容为空"}

    from ..idempotency import generate_idempotency_key, check_idempotency
    _idem_key = generate_idempotency_key(db, "create_worldbuilding_entry", project_id, args)
    if _idem_key:
        _existing = check_idempotency(db, project_id, _idem_key)
        if _existing:
            return _existing
    if args.get("related_characters") or args.get("plot_usage") or args.get("constraints"):
        extras = []
        related = args.get("related_characters")
        constraints = args.get("constraints")
        if isinstance(related, list) and related:
            extras.append("关联角色：" + "、".join(str(item) for item in related if item))
        if args.get("plot_usage"):
            extras.append("剧情用途：" + str(args.get("plot_usage")))
        if isinstance(constraints, list) and constraints:
            extras.append("限制条件：" + "；".join(str(item) for item in constraints if item))
        if extras:
            content = f"{content}\n\n" + "\n".join(extras)
    entry = WorldbuildingEntry(
        project_id=project_id,
        dimension=dimension,
        title=title[:200],
        content=content[:12000],
        sort_order=int(
            args.get("sort_order")
            if args.get("sort_order") is not None
            else next_worldbuilding_sort_order(db, project_id, dimension)
        ),
        status=str(args.get("status") or "active")[:30],
        confidence=float(args.get("confidence")) if args.get("confidence") is not None else None,
        first_seen_chapter_id=str(args.get("first_seen_chapter_id") or "")[:36] or None,
        last_updated_chapter_id=str(args.get("last_updated_chapter_id") or "")[:36] or None,
    )
    db.add(entry)
    db.flush()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_worldbuilding_to_file(db, project, entry)
        db.flush()
    return {
        "tool": "create_worldbuilding_entry",
        "status": "ok",
        "detail": f"已创建世界观：{entry.title}",
        "data": worldbuilding_payload(entry),
    }


async def update_worldbuilding_entry(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    entry = find_worldbuilding_by_title_or_id(db, project_id, args.get("id") or args.get("title"))
    if not entry:
        return {"tool": "update_worldbuilding_entry", "status": "skipped", "detail": "未找到世界观条目"}
    if args.get("dimension") in WORLD_DIMENSIONS:
        entry.dimension = str(args.get("dimension"))
    if args.get("title"):
        entry.title = str(args.get("title")).strip()[:200]
    if "content" in args:
        entry.content = str(args.get("content") or "")[:12000]
    if args.get("sort_order") is not None:
        entry.sort_order = int(args.get("sort_order"))
    if "status" in args:
        entry.status = str(args.get("status") or "active")[:30]
    if "confidence" in args:
        entry.confidence = float(args.get("confidence")) if args.get("confidence") is not None else None
    if "first_seen_chapter_id" in args:
        entry.first_seen_chapter_id = str(args.get("first_seen_chapter_id") or "")[:36] or None
    if "last_updated_chapter_id" in args:
        entry.last_updated_chapter_id = str(args.get("last_updated_chapter_id") or "")[:36] or None
    entry.updated_at = datetime.utcnow()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_worldbuilding_to_file(db, project, entry)
        db.flush()
    return {
        "tool": "update_worldbuilding_entry",
        "status": "ok",
        "detail": f"已更新世界观：{entry.title}",
        "data": worldbuilding_payload(entry),
    }


async def delete_worldbuilding_entry(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    entry = find_worldbuilding_by_title_or_id(db, project_id, args.get("id") or args.get("title"))
    if not entry:
        return {"tool": "delete_worldbuilding_entry", "status": "skipped", "detail": "未找到世界观条目"}
    title = entry.title
    project = db.query(Project).filter(Project.id == project_id).first()
    content_file_path = entry.content_file_path
    db.delete(entry)
    if project:
        delete_project_file(project, content_file_path)
    db.flush()
    return {"tool": "delete_worldbuilding_entry", "status": "ok", "detail": f"已删除世界观：{title}"}
