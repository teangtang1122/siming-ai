"""Authoritative chapter-character association mutations.

HTTP, workspace, and future mobile adapters should call this boundary instead
of reproducing ORM queries or provenance repair rules in their entry points.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.db_helpers import get_character_or_404, get_project_or_404
from ..core.exceptions import NotFoundError
from ..database.models import (
    Chapter,
    ChapterCharacter,
    CharacterVersion,
    OutlineNode,
    OutlineNodeCharacter,
)


def remove_character_chapter_appearance(
    db: Session,
    project_id: str,
    character_id: str,
    chapter_id: str,
) -> dict:
    """Remove one rejected association and repair its derived provenance."""

    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.id == chapter_id)
        .first()
    )
    if chapter is None:
        raise NotFoundError("章节不存在")

    appearance = (
        db.query(ChapterCharacter)
        .filter(
            ChapterCharacter.chapter_id == chapter_id,
            ChapterCharacter.character_id == character_id,
        )
        .first()
    )
    if appearance is None:
        raise NotFoundError("该角色没有此章节关联")
    db.delete(appearance)

    chapter_outline_ids = {
        node_id
        for (node_id,) in (
            db.query(OutlineNode.id)
            .filter(
                OutlineNode.project_id == project_id,
                (
                    (OutlineNode.source_chapter_id == chapter_id)
                    | (OutlineNode.id == chapter.outline_node_id)
                ),
            )
            .all()
        )
    }
    removed_outline_links = 0
    if chapter_outline_ids:
        removed_outline_links = (
            db.query(OutlineNodeCharacter)
            .filter(
                OutlineNodeCharacter.character_id == character_id,
                OutlineNodeCharacter.outline_node_id.in_(chapter_outline_ids),
            )
            .delete(synchronize_session=False)
        )

    db.flush()
    if character.last_seen_chapter_id == chapter_id:
        prior_appearance = (
            db.query(Chapter)
            .join(ChapterCharacter, ChapterCharacter.chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                ChapterCharacter.character_id == character_id,
            )
            .order_by(Chapter.sort_order.desc(), Chapter.created_at.desc())
            .first()
        )
        character.last_seen_chapter_id = prior_appearance.id if prior_appearance else None
    if character.last_updated_chapter_id == chapter_id:
        prior_version = (
            db.query(CharacterVersion)
            .filter(
                CharacterVersion.character_id == character_id,
                CharacterVersion.source_chapter_id.is_not(None),
                CharacterVersion.source_chapter_id != chapter_id,
            )
            .order_by(CharacterVersion.version_number.desc())
            .first()
        )
        character.last_updated_chapter_id = (
            prior_version.source_chapter_id if prior_version else None
        )

    return {
        "character_id": character_id,
        "chapter_id": chapter_id,
        "removed_chapter_links": 1,
        "removed_outline_links": removed_outline_links,
        "last_seen_chapter_id": character.last_seen_chapter_id,
        "last_updated_chapter_id": character.last_updated_chapter_id,
    }


def upsert_character_chapter_appearance(
    db: Session,
    project_id: str,
    character_id: str,
    chapter_id: str,
    *,
    appearance_type: str | None,
    description: str | None,
) -> dict:
    """Create or update the single author-confirmed chapter/person link."""

    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.id == chapter_id)
        .first()
    )
    if chapter is None:
        raise NotFoundError("章节不存在")

    appearance = (
        db.query(ChapterCharacter)
        .filter(
            ChapterCharacter.chapter_id == chapter_id,
            ChapterCharacter.character_id == character_id,
        )
        .first()
    )
    created = appearance is None
    if appearance is None:
        appearance = ChapterCharacter(
            chapter_id=chapter_id,
            character_id=character_id,
        )
        db.add(appearance)
    appearance.appearance_type = appearance_type
    appearance.description = description

    latest_seen = None
    if character.last_seen_chapter_id:
        latest_seen = (
            db.query(Chapter)
            .filter(
                Chapter.project_id == project_id,
                Chapter.id == character.last_seen_chapter_id,
            )
            .first()
        )
    if latest_seen is None or (
        int(chapter.sort_order or 0),
        chapter.created_at,
    ) >= (
        int(latest_seen.sort_order or 0),
        latest_seen.created_at,
    ):
        character.last_seen_chapter_id = chapter.id
    db.flush()

    return {
        "id": appearance.id,
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "character_id": character.id,
        "character_name": character.name,
        "appearance_type": appearance.appearance_type,
        "description": appearance.description,
        "created": created,
    }


__all__ = [
    "remove_character_chapter_appearance",
    "upsert_character_chapter_appearance",
]
