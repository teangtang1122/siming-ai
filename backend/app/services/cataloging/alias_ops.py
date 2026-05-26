"""Character alias helpers for cataloging."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ...database.models import Character, CharacterAlias, Chapter
from .merge import merge_short_text


def ensure_character_alias(
    db: Session,
    character: Character,
    alias: str,
    chapter: Chapter | None = None,
    alias_type: str = "alias",
    description: str | None = None,
    confidence: float | None = None,
    merged_character_id: str | None = None,
) -> CharacterAlias | None:
    text = str(alias or "").strip()
    if not text:
        return None
    row = (
        db.query(CharacterAlias)
        .filter(
            CharacterAlias.project_id == character.project_id,
            CharacterAlias.character_id == character.id,
            CharacterAlias.alias == text[:200],
        )
        .first()
    )
    if not row:
        row = CharacterAlias(
            project_id=character.project_id,
            character_id=character.id,
            alias=text[:200],
            alias_type=alias_type[:50] or "alias",
            source_chapter_id=chapter.id if chapter else None,
        )
        db.add(row)
        db.flush()
    row.alias_type = alias_type[:50] or row.alias_type
    row.description = merge_short_text(row.description, description, chapter, limit=3000) if description else row.description
    row.confidence = confidence if confidence is not None else row.confidence
    row.merged_character_id = merged_character_id or row.merged_character_id
    row.updated_at = datetime.utcnow()
    return row


def aliases_for_character(character: Character) -> list[str]:
    return [item.alias for item in (character.aliases or []) if item.alias]
