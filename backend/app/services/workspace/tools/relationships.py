"""Relationship workspace tools."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import CharacterRelationship, Project
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ...character_relationships import collapse_directed_relationship_pair
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

    rel, removed_ids = collapse_directed_relationship_pair(
        db,
        project_id,
        source.id,
        target.id,
    )
    created = rel is None
    if rel is None:
        rel = CharacterRelationship(
            project_id=project_id,
            character_a_id=source.id,
            character_b_id=target.id,
            relationship_type=str(args.get("relationship_type") or "关联")[:100],
            description=str(args.get("description") or "")[:4000],
        )
        db.add(rel)
    else:
        rel.relationship_type = str(args.get("relationship_type") or "关联")[:100]
        rel.description = str(args.get("description") or "")[:4000]
    db.flush()
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
                source="workspace_tool",
            ),
        )
    return {
        "tool": "create_relationship",
        "status": "ok",
        "detail": f"已{'创建' if created else '更新'}关系：{source.name} - {target.name}",
        "data": {
            "relationship_id": rel.id,
            "created": created,
            "deduplicated_relationship_ids": removed_ids,
        },
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
    rel, removed_ids = collapse_directed_relationship_pair(
        db,
        project_id,
        source.id,
        target.id,
    )
    if not rel:
        return {"tool": "update_relationship", "status": "skipped", "detail": "未找到关系"}
    if args.get("relationship_type"):
        rel.relationship_type = str(args.get("relationship_type"))[:100]
    if "description" in args:
        rel.description = str(args.get("description") or "")[:4000]
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
                source="workspace_tool",
            ),
        )
    return {
        "tool": "update_relationship",
        "status": "ok",
        "detail": f"已更新关系：{source.name} - {target.name}",
        "data": {
            "relationship_id": rel.id,
            "deduplicated_relationship_ids": removed_ids,
        },
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
    relationships = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            CharacterRelationship.character_a_id == source.id,
            CharacterRelationship.character_b_id == target.id,
        )
        .all()
    )
    if not relationships:
        return {"tool": "delete_relationship", "status": "skipped", "detail": "未找到关系"}
    deleted_ids = [str(rel.id) for rel in relationships]
    for rel in relationships:
        db.delete(rel)
    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
                source="workspace_tool",
            ),
        )
    return {
        "tool": "delete_relationship",
        "status": "ok",
        "detail": f"已删除关系：{source.name} - {target.name}",
        "data": {"deleted_relationship_ids": deleted_ids},
    }
