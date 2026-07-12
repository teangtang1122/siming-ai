"""Character business logic — versioning, appearances, change logs, and data formatting."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from ..database.models import (
    Chapter,
    ChapterCharacter,
    Character,
    CharacterAlias,
    CharacterChangeLog,
    CharacterVersion,
    OutlineNode,
    OutlineNodeCharacter,
)
from ..schemas.character import CharacterResponse


def loads_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def dumps_list(value: Optional[list[str]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def character_to_dict(character: Character) -> dict:
    data = CharacterResponse(
        id=character.id,
        project_id=character.project_id,
        name=character.name,
        appearance=character.appearance,
        personality=character.personality,
        background=character.background,
        abilities=loads_list(character.abilities),
        aliases=character_aliases(character),
        role_type=character.role_type,
        age=character.age,
        life_status=character.life_status,
        current_location=character.current_location,
        realm_or_level=character.realm_or_level,
        physical_state=character.physical_state,
        mental_state=character.mental_state,
        current_goal=character.current_goal,
        active_conflict=character.active_conflict,
        abilities_state=character.abilities_state,
        items_or_assets=character.items_or_assets,
        profile=character.profile_json,
        last_seen_chapter_id=character.last_seen_chapter_id,
        last_updated_chapter_id=character.last_updated_chapter_id,
        current_version=character.current_version,
        is_evolution_tracked=character.is_evolution_tracked,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )
    return data.model_dump(mode="json")


def character_aliases(character: Character) -> list[str]:
    return [alias.alias for alias in (character.aliases or []) if alias.alias]


def sync_character_aliases(db: Session, character: Character, aliases: Optional[list[str]]) -> None:
    if aliases is None:
        return
    cleaned = []
    seen = set()
    for item in aliases:
        text = str(item or "").strip()
        if not text or text == character.name or text in seen:
            continue
        seen.add(text)
        cleaned.append(text[:200])

    existing = {
        item.alias: item
        for item in db.query(CharacterAlias).filter(CharacterAlias.character_id == character.id).all()
        if item.alias_type == "alias"
    }
    for alias, row in existing.items():
        if alias not in seen:
            db.delete(row)
    for alias in cleaned:
        if alias not in existing:
            db.add(CharacterAlias(
                project_id=character.project_id,
                character_id=character.id,
                alias=alias,
                alias_type="alias",
            ))


def snapshot_character(character: Character) -> dict:
    return {
        "id": character.id,
        "project_id": character.project_id,
        "name": character.name,
        "appearance": character.appearance,
        "personality": character.personality,
        "background": character.background,
        "abilities": loads_list(character.abilities),
        "aliases": character_aliases(character),
        "role_type": character.role_type,
        "age": character.age,
        "life_status": character.life_status,
        "current_location": character.current_location,
        "realm_or_level": character.realm_or_level,
        "physical_state": character.physical_state,
        "mental_state": character.mental_state,
        "current_goal": character.current_goal,
        "active_conflict": character.active_conflict,
        "abilities_state": character.abilities_state,
        "items_or_assets": character.items_or_assets,
        "profile": character.profile_json,
        "last_seen_chapter_id": character.last_seen_chapter_id,
        "last_updated_chapter_id": character.last_updated_chapter_id,
        "current_version": character.current_version,
        "is_evolution_tracked": character.is_evolution_tracked,
        "created_at": character.created_at.isoformat() if character.created_at else None,
        "updated_at": character.updated_at.isoformat() if character.updated_at else None,
    }


def create_character_version(
    db: Session,
    character: Character,
    change_summary: str,
    source_chapter_id: Optional[str] = None,
) -> None:
    character.current_version = (character.current_version or 1) + 1
    db.flush()
    db.add(CharacterVersion(
        character_id=character.id,
        version_number=character.current_version,
        snapshot_data=json.dumps(snapshot_character(character), ensure_ascii=False),
        change_summary=change_summary,
        source_chapter_id=source_chapter_id,
    ))


def get_appearances(db: Session, character_id: str) -> dict:
    outline_rows = (
        db.query(OutlineNode, OutlineNodeCharacter)
        .join(OutlineNodeCharacter, OutlineNodeCharacter.outline_node_id == OutlineNode.id)
        .filter(OutlineNodeCharacter.character_id == character_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )
    chapter_rows = (
        db.query(Chapter, ChapterCharacter)
        .join(ChapterCharacter, ChapterCharacter.chapter_id == Chapter.id)
        .filter(ChapterCharacter.character_id == character_id)
        .order_by(Chapter.updated_at.desc())
        .all()
    )
    return {
        "outline_nodes": [
            {
                "id": node.id,
                "title": node.title,
                "node_type": node.node_type,
                "status": node.status,
                "role_in_scene": link.role_in_scene,
            }
            for node, link in outline_rows
        ],
        "chapters": [
            {
                "id": chapter.id,
                "title": chapter.title,
                "word_count": chapter.word_count,
                "appearance_type": link.appearance_type,
                "description": link.description,
            }
            for chapter, link in chapter_rows
        ],
    }


def apply_change_log_to_character(character: Character, log: CharacterChangeLog) -> bool:
    """Apply a confirmed change log to a character profile when supported."""
    if not log.new_value:
        return False

    if log.field_name == "abilities":
        abilities = loads_list(character.abilities)
        try:
            parsed = json.loads(log.new_value)
        except json.JSONDecodeError:
            parsed = [log.new_value]
        if isinstance(parsed, str):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return False
        changed = False
        for item in parsed:
            value = str(item).strip()
            if value and value not in abilities:
                abilities.append(value)
                changed = True
        if changed:
            character.abilities = json.dumps(abilities, ensure_ascii=False)
        return changed

    if log.field_name == "personality":
        character.personality = log.new_value[:2000]
        return True
    if log.field_name == "background":
        character.background = log.new_value[:5000]
        return True
    if log.field_name == "appearance":
        character.appearance = log.new_value[:2000]
        return True
    return False
