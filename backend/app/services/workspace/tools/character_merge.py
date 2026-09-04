"""Character merge workspace tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....services.character_merge_service import (
    build_character_merge_preview,
    find_duplicate_character_candidates,
    merge_characters,
)


async def list_duplicate_characters(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    limit = max(1, min(100, int(args.get("limit") or 80)))
    items = find_duplicate_character_candidates(db, project_id, limit=limit)
    return {
        "tool": "list_duplicate_characters",
        "status": "ok",
        "detail": f"发现 {len(items)} 组疑似重复角色",
        "data": {"items": items, "total": len(items)},
    }


async def preview_character_merge(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    primary_id = str(args.get("primary_id") or "").strip()
    secondary_id = str(args.get("secondary_id") or "").strip()
    if not primary_id or not secondary_id:
        return {
            "tool": "preview_character_merge",
            "status": "skipped",
            "detail": "缺少 primary_id 或 secondary_id",
        }
    try:
        data = build_character_merge_preview(db, project_id, primary_id, secondary_id, args)
    except ValueError:
        return {
            "tool": "preview_character_merge",
            "status": "skipped",
            "detail": "角色合并预览参数无效或角色不属于当前作品。",
        }
    return {
        "tool": "preview_character_merge",
        "status": "ok",
        "detail": "已生成角色合并预览",
        "data": data,
    }


async def merge_duplicate_characters(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    primary_id = str(args.get("primary_id") or "").strip()
    secondary_id = str(args.get("secondary_id") or "").strip()
    if not primary_id or not secondary_id:
        return {
            "tool": "merge_duplicate_characters",
            "status": "skipped",
            "detail": "缺少 primary_id 或 secondary_id",
        }
    try:
        data = merge_characters(db, project_id, primary_id, secondary_id, args)
    except ValueError:
        return {
            "tool": "merge_duplicate_characters",
            "status": "skipped",
            "detail": "角色合并参数无效或角色不属于当前作品。",
        }
    db.flush()
    return {
        "tool": "merge_duplicate_characters",
        "status": "ok",
        "detail": "角色已合并",
        "data": data,
    }
