"""Character workspace tools."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Character, CharacterVersion
from ..types import WorkspaceActionDependencies
from ..utils import character_payload, find_character_by_name_or_id


async def create_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"tool": "create_character", "status": "skipped", "detail": "角色名为空"}
    character = Character(
        project_id=project_id,
        name=name[:100],
        appearance=str(args.get("appearance") or "")[:4000],
        personality=str(args.get("personality") or "")[:4000],
        background=str(args.get("background") or "")[:8000],
        abilities=json.dumps(
            args.get("abilities") if isinstance(args.get("abilities"), list) else [],
            ensure_ascii=False,
        ),
        role_type=str(args.get("role_type") or "supporting"),
        is_evolution_tracked=True,
    )
    db.add(character)
    db.flush()
    return {
        "tool": "create_character",
        "status": "ok",
        "detail": f"已创建角色：{character.name}",
        "data": character_payload(character),
    }


async def update_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
) -> dict:
    character = find_character_by_name_or_id(db, project_id, args.get("id") or args.get("name"))
    if not character:
        return {"tool": "update_character", "status": "skipped", "detail": "未找到角色"}
    changed = False
    for field, limit in [("appearance", 4000), ("personality", 4000), ("background", 8000), ("role_type", 100)]:
        if field in args:
            setattr(character, field, str(args.get(field) or "")[:limit])
            changed = True
    if "abilities" in args and isinstance(args.get("abilities"), list):
        character.abilities = json.dumps(args.get("abilities"), ensure_ascii=False)
        changed = True
    if changed:
        character.current_version = (character.current_version or 1) + 1
        character.updated_at = datetime.utcnow()
        db.add(CharacterVersion(
            character_id=character.id,
            version_number=character.current_version,
            snapshot_data=json.dumps(character_payload(character), ensure_ascii=False),
            change_summary="AI助手调整角色档案",
        ))
    return {
        "tool": "update_character",
        "status": "ok",
        "detail": f"已更新角色：{character.name}",
        "data": character_payload(character),
    }

