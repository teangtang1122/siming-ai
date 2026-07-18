"""SQLAlchemy adapter for character HTTP use cases."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.models import (
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterVersion,
)


class SqlAlchemyCharacterWorkspace:
    def __init__(self, session: Session) -> None:
        self.db = session

    def list_characters(self, project_id: str, query: str | None = None):
        rows = self.db.query(Character).filter(
            Character.project_id == project_id,
            or_(Character.role_type.is_(None), Character.role_type != "merged_alias"),
        )
        if query:
            keyword = f"%{query}%"
            alias_ids = [
                row.character_id
                for row in self.db.query(CharacterAlias.character_id).filter(
                    CharacterAlias.project_id == project_id,
                    CharacterAlias.alias.like(keyword),
                ).all()
            ]
            rows = rows.filter(
                or_(
                    Character.name.like(keyword),
                    Character.appearance.like(keyword),
                    Character.personality.like(keyword),
                    Character.background.like(keyword),
                    Character.role_type.like(keyword),
                    Character.id.in_(alias_ids) if alias_ids else False,
                )
            )
        return rows.order_by(Character.updated_at.desc()).all()

    def create_character(self, **values: Any):
        character = Character(**values)
        self.db.add(character)
        return character

    def relationship_network(self, project_id: str):
        characters = self.db.query(Character).filter(
            Character.project_id == project_id,
            or_(Character.role_type.is_(None), Character.role_type != "merged_alias"),
        ).all()
        relationships = self.db.query(CharacterRelationship).filter(
            CharacterRelationship.project_id == project_id
        ).order_by(CharacterRelationship.created_at.asc()).all()
        return characters, relationships

    def create_version(self, **values: Any):
        version = CharacterVersion(**values)
        self.db.add(version)
        return version

    def delete_relationships(self, project_id: str, character_id: str) -> None:
        self.db.query(CharacterRelationship).filter(
            CharacterRelationship.project_id == project_id,
            or_(
                CharacterRelationship.character_a_id == character_id,
                CharacterRelationship.character_b_id == character_id,
            ),
        ).delete(synchronize_session=False)

    def delete(self, value: Any) -> None:
        self.db.delete(value)

    def versions(self, character_id: str):
        return self.db.query(CharacterVersion).filter(
            CharacterVersion.character_id == character_id
        ).order_by(CharacterVersion.version_number.desc()).all()

    def version(self, character_id: str, version_id: str):
        return self.db.query(CharacterVersion).filter(
            CharacterVersion.id == version_id,
            CharacterVersion.character_id == character_id,
        ).first()

    def targets_exist(self, project_id: str, target_ids: set[str]) -> bool:
        if not target_ids:
            return True
        count = self.db.query(Character).filter(
            Character.project_id == project_id,
            Character.id.in_(target_ids),
        ).count()
        return count == len(target_ids)

    def replace_relationships(
        self,
        project_id: str,
        character_id: str,
        relationships: Sequence[Any],
    ) -> None:
        self.delete_relationships(project_id, character_id)
        for item in relationships:
            self.db.add(
                CharacterRelationship(
                    project_id=project_id,
                    character_a_id=character_id,
                    character_b_id=item.target_character_id,
                    relationship_type=item.relationship_type,
                    description=item.description,
                )
            )

    def ensure_ai_config(self, character: Any):
        config = character.ai_config
        if config:
            return config
        config = CharacterAIConfig(character_id=character.id)
        self.db.add(config)
        return config

    def change_logs(
        self,
        project_id: str,
        *,
        chapter_id: str | None = None,
        character_id: str | None = None,
        confirmed: bool | None = None,
        limit: int = 200,
    ):
        query = self.db.query(CharacterChangeLog).join(
            Character,
            CharacterChangeLog.character_id == Character.id,
        ).filter(Character.project_id == project_id)
        if chapter_id:
            query = query.filter(CharacterChangeLog.chapter_id == chapter_id)
        if character_id:
            query = query.filter(CharacterChangeLog.character_id == character_id)
        if confirmed is not None:
            query = query.filter(CharacterChangeLog.confirmed == confirmed)
        return query.order_by(CharacterChangeLog.created_at.desc()).limit(limit).all()

    def change_log(self, project_id: str, log_id: str):
        return self.db.query(CharacterChangeLog).join(
            Character,
            CharacterChangeLog.character_id == Character.id,
        ).filter(
            CharacterChangeLog.id == log_id,
            Character.project_id == project_id,
        ).first()

    def character(self, character_id: str):
        return self.db.query(Character).filter(Character.id == character_id).first()

    def pending_change_logs(
        self,
        project_id: str,
        *,
        chapter_id: str | None = None,
        character_id: str | None = None,
    ):
        return self.change_logs(
            project_id,
            chapter_id=chapter_id,
            character_id=character_id,
            confirmed=False,
            limit=100000,
        )

    def serialize_change_logs(self, logs: Sequence[Any]) -> list[dict]:
        items: list[dict] = []
        for log in logs:
            character = self.character(log.character_id)
            chapter = self.db.query(Chapter).filter(Chapter.id == log.chapter_id).first()
            items.append(
                {
                    "id": log.id,
                    "character_id": log.character_id,
                    "character_name": character.name if character else "未知",
                    "chapter_id": log.chapter_id,
                    "chapter_title": chapter.title if chapter else "未知",
                    "change_type": log.change_type,
                    "field_name": log.field_name,
                    "old_value": log.old_value,
                    "new_value": log.new_value,
                    "confirmed": log.confirmed,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
            )
        return items


__all__ = ["SqlAlchemyCharacterWorkspace"]
