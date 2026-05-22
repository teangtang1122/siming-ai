"""Worldbuilding workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import WorldbuildingEntry
from ..types import WorkspaceActionDependencies
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
    deps: WorkspaceActionDependencies,
) -> dict:
    dimension = str(args.get("dimension") or "culture").strip()
    if dimension not in WORLD_DIMENSIONS:
        dimension = "culture"
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    if not title or not content:
        return {"tool": "create_worldbuilding_entry", "status": "skipped", "detail": "世界观标题或内容为空"}
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
    )
    db.add(entry)
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
    deps: WorkspaceActionDependencies,
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
    entry.updated_at = datetime.utcnow()
    return {
        "tool": "update_worldbuilding_entry",
        "status": "ok",
        "detail": f"已更新世界观：{entry.title}",
        "data": worldbuilding_payload(entry),
    }

