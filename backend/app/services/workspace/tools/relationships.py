"""Relationship workspace tools."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import CharacterRelationship, Project
from ....services.content_store import sync_relationships_to_file
from ..utils import find_character_by_name_or_id


async def create_relationship(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    source = find_character_by_name_or_id(db, project_id, args.get("source") or args.get("from"))
    target = find_character_by_name_or_id(db, project_id, args.get("target") or args.get("to"))
    if not source or not target or source.id == target.id:
        return {"tool": "create_relationship", "status": "skipped", "detail": "关系角色无效"}

    from ..idempotency import generate_idempotency_key, check_idempotency
    _idem_key = generate_idempotency_key(db, "create_relationship", project_id, args)
    if _idem_key:
        _existing = check_idempotency(db, project_id, _idem_key)
        if _existing:
            return _existing
    rel = CharacterRelationship(
        project_id=project_id,
        character_a_id=source.id,
        character_b_id=target.id,
        relationship_type=str(args.get("relationship_type") or "关联")[:100],
        description=str(args.get("description") or "")[:4000],
    )
    db.add(rel)
    db.flush()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_relationships_to_file(db, project)
        db.flush()
    return {
        "tool": "create_relationship",
        "status": "ok",
        "detail": f"已创建关系：{source.name} - {target.name}",
    }


async def update_relationship(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    source = find_character_by_name_or_id(db, project_id, args.get("source") or args.get("from"))
    target = find_character_by_name_or_id(db, project_id, args.get("target") or args.get("to"))
    if not source or not target:
        return {"tool": "update_relationship", "status": "skipped", "detail": "未找到关系角色"}
    rel = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            CharacterRelationship.character_a_id == source.id,
            CharacterRelationship.character_b_id == target.id,
        )
        .first()
    )
    if not rel:
        return {"tool": "update_relationship", "status": "skipped", "detail": "未找到关系"}
    if args.get("relationship_type"):
        rel.relationship_type = str(args.get("relationship_type"))[:100]
    if "description" in args:
        rel.description = str(args.get("description") or "")[:4000]
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        sync_relationships_to_file(db, project)
        db.flush()
    return {
        "tool": "update_relationship",
        "status": "ok",
        "detail": f"已更新关系：{source.name} - {target.name}",
    }


async def delete_relationship(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    source = find_character_by_name_or_id(db, project_id, args.get("source") or args.get("from"))
    target = find_character_by_name_or_id(db, project_id, args.get("target") or args.get("to"))
    if not source or not target:
        return {"tool": "delete_relationship", "status": "skipped", "detail": "未找到关系角色"}
    rel = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            CharacterRelationship.character_a_id == source.id,
            CharacterRelationship.character_b_id == target.id,
        )
        .first()
    )
    if not rel:
        return {"tool": "delete_relationship", "status": "skipped", "detail": "未找到关系"}
    db.delete(rel)
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        db.flush()
        sync_relationships_to_file(db, project)
    db.flush()
    return {
        "tool": "delete_relationship",
        "status": "ok",
        "detail": f"已删除关系：{source.name} - {target.name}",
    }
