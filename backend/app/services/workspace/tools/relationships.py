"""Relationship workspace tools."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import CharacterRelationship
from ..types import WorkspaceActionDependencies
from ..utils import find_character_by_name_or_id


async def create_relationship(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    deps: WorkspaceActionDependencies,
) -> dict:
    source = find_character_by_name_or_id(db, project_id, args.get("source") or args.get("from"))
    target = find_character_by_name_or_id(db, project_id, args.get("target") or args.get("to"))
    if not source or not target or source.id == target.id:
        return {"tool": "create_relationship", "status": "skipped", "detail": "关系角色无效"}
    rel = CharacterRelationship(
        project_id=project_id,
        character_a_id=source.id,
        character_b_id=target.id,
        relationship_type=str(args.get("relationship_type") or "关联")[:100],
        description=str(args.get("description") or "")[:4000],
    )
    db.add(rel)
    db.flush()
    return {
        "tool": "create_relationship",
        "status": "ok",
        "detail": f"已创建关系：{source.name} - {target.name}",
    }

