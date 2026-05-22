"""Shared helpers for workspace assistant tools."""
from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from ...database.models import (
    Character,
    OutlineNode,
    OutlineNodeCharacter,
    WorldbuildingEntry,
)


WORLD_DIMENSIONS = {"geography", "history", "factions", "power_system", "races", "culture"}


def character_payload(character: Character) -> dict:
    abilities: list[str] = []
    if character.abilities:
        try:
            parsed = json.loads(character.abilities)
            abilities = parsed if isinstance(parsed, list) else []
        except Exception:
            abilities = [part.strip() for part in character.abilities.split(",") if part.strip()]
    return {
        "id": character.id,
        "name": character.name,
        "appearance": character.appearance,
        "personality": character.personality,
        "background": character.background,
        "abilities": abilities,
        "role_type": character.role_type,
        "current_version": character.current_version,
    }


def outline_node_payload(node: OutlineNode) -> dict:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "node_type": node.node_type,
        "title": node.title,
        "summary": node.summary,
        "status": node.status,
        "sort_order": node.sort_order,
        "linked_characters": [
            {"id": link.character.id, "name": link.character.name, "role_in_scene": link.role_in_scene}
            for link in node.linked_characters
            if link.character
        ],
    }


def worldbuilding_payload(entry: WorldbuildingEntry) -> dict:
    return {
        "id": entry.id,
        "dimension": entry.dimension,
        "title": entry.title,
        "content": entry.content,
        "sort_order": entry.sort_order,
    }


def find_worldbuilding_by_title_or_id(
    db: Session,
    project_id: str,
    value: object,
) -> Optional[WorldbuildingEntry]:
    text = str(value or "").strip()
    if not text:
        return None
    return (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .filter((WorldbuildingEntry.id == text) | (WorldbuildingEntry.title == text))
        .first()
    )


def find_character_by_name_or_id(db: Session, project_id: str, value: object) -> Optional[Character]:
    text = str(value or "").strip()
    if not text:
        return None
    return (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter((Character.id == text) | (Character.name == text))
        .first()
    )


def normalize_outline_lookup(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s:：,，.。;；!！?？()（）【】\[\]《》<>\"'“”‘’_-]+", "", text)


def find_outline_by_title_or_id(db: Session, project_id: str, value: object) -> Optional[OutlineNode]:
    text = str(value or "").strip()
    if not text:
        return None
    base_query = (
        db.query(OutlineNode)
        .options(selectinload(OutlineNode.linked_characters).selectinload(OutlineNodeCharacter.character))
        .filter(OutlineNode.project_id == project_id)
    )
    exact = (
        base_query
        .filter((OutlineNode.id == text) | (OutlineNode.title == text))
        .order_by(OutlineNode.updated_at.desc())
        .first()
    )
    if exact:
        return exact
    normalized = normalize_outline_lookup(text)
    if not normalized:
        return None
    candidates = (
        base_query
        .order_by(OutlineNode.updated_at.desc(), OutlineNode.sort_order.desc())
        .all()
    )
    for node in candidates:
        if normalize_outline_lookup(node.title) == normalized:
            return node
    for node in candidates:
        node_title = normalize_outline_lookup(node.title)
        if node_title and (normalized in node_title or node_title in normalized):
            return node
    return None


def character_ids_from_names(db: Session, project_id: str, names: object) -> list[str]:
    if not isinstance(names, list):
        return []
    ids = []
    for name in names:
        character = find_character_by_name_or_id(db, project_id, name)
        if character and character.id not in ids:
            ids.append(character.id)
    return ids


def replace_outline_links_by_names(
    db: Session,
    project_id: str,
    node: OutlineNode,
    names: object,
) -> None:
    ids = character_ids_from_names(db, project_id, names)
    if not ids:
        return
    node.linked_characters.clear()
    db.flush()
    for character_id in ids:
        node.linked_characters.append(OutlineNodeCharacter(character_id=character_id, role_in_scene="AI关联"))


def next_outline_sort_order(db: Session, project_id: str, parent_id: Optional[str]) -> int:
    last = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.parent_id == parent_id)
        .order_by(OutlineNode.sort_order.desc(), OutlineNode.created_at.desc())
        .first()
    )
    return (last.sort_order + 1) if last and last.sort_order is not None else 0


def next_worldbuilding_sort_order(db: Session, project_id: str, dimension: str) -> int:
    last = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id, WorldbuildingEntry.dimension == dimension)
        .order_by(WorldbuildingEntry.sort_order.desc(), WorldbuildingEntry.created_at.desc())
        .first()
    )
    return (last.sort_order + 1) if last and last.sort_order is not None else 0


# Backward-compatible names for existing router/tests while the larger refactor proceeds.
_character_payload = character_payload
_outline_node_payload = outline_node_payload
_worldbuilding_payload = worldbuilding_payload
_find_worldbuilding_by_title_or_id = find_worldbuilding_by_title_or_id
_find_character_by_name_or_id = find_character_by_name_or_id
_normalize_outline_lookup = normalize_outline_lookup
_find_outline_by_title_or_id = find_outline_by_title_or_id
_character_ids_from_names = character_ids_from_names
_replace_outline_links_by_names = replace_outline_links_by_names
_next_outline_sort_order = next_outline_sort_order
_next_worldbuilding_sort_order = next_worldbuilding_sort_order

