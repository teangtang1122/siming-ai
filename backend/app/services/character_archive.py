"""Canonical character-card projection used by every writing route.

Cataloging persists one character across several tables/columns: the base card,
current state, deep profile, aliases, AI voice configuration, and relationships.
Consumers must not each invent a smaller projection or API and CLI writing will
silently receive different characters.  This module is the single read model.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database.models import Character, CharacterRelationship
from .character_role_types import normalize_character_role_type

CHARACTER_PROFILE_FIELDS: tuple[str, ...] = (
    "core_motivation",
    "inner_lack",
    "core_belief",
    "public_persona",
    "hidden_persona",
    "reveal_chapter",
    "moral_taboo",
    "voice",
    "action_habit",
    "trauma_trigger",
)


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _abilities(character: Character) -> list[str]:
    parsed = _json_list(character.abilities)
    if parsed:
        return parsed
    value = str(character.abilities or "").strip()
    return [value] if value else []


def _relationship_payloads(
    db: Session,
    character: Character,
) -> list[dict[str, Any]]:
    rows = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == character.project_id,
            or_(
                CharacterRelationship.character_a_id == character.id,
                CharacterRelationship.character_b_id == character.id,
            ),
        )
        .order_by(CharacterRelationship.created_at.asc(), CharacterRelationship.id.asc())
        .all()
    )
    if not rows:
        return []
    character_ids = {
        endpoint
        for row in rows
        for endpoint in (row.character_a_id, row.character_b_id)
        if endpoint
    }
    names = dict(
        db.query(Character.id, Character.name)
        .filter(Character.id.in_(character_ids))
        .all()
    )
    return [
        {
            "id": row.id,
            "source_id": row.character_a_id,
            "source_name": names.get(row.character_a_id, row.character_a_id),
            "target_id": row.character_b_id,
            "target_name": names.get(row.character_b_id, row.character_b_id),
            "relationship_type": row.relationship_type,
            "description": row.description or "",
        }
        for row in rows
    ]


def character_archive_payload(
    character: Character,
    *,
    db: Session | None = None,
    target_chapter_number: int | None = None,
) -> dict[str, Any]:
    """Return the complete stable character card, optionally with relations."""
    profile = (
        dict(character.profile_json)
        if isinstance(character.profile_json, dict)
        else {}
    )
    # Keep unknown future profile keys during round trips, while ensuring the
    # currently supported writing-lock fields always have a stable shape.
    for field in CHARACTER_PROFILE_FIELDS:
        profile.setdefault(field, "")

    config = character.ai_config
    payload: dict[str, Any] = {
        "id": character.id,
        "name": character.name,
        "aliases": [item.alias for item in (character.aliases or []) if item.alias],
        "role_type": normalize_character_role_type(character.role_type) or "",
        "age": character.age or "",
        "appearance": character.appearance or "",
        "personality": character.personality or "",
        "background": character.background or "",
        "abilities": _abilities(character),
        "state": {
            "life_status": character.life_status or "",
            "current_location": character.current_location or "",
            "realm_or_level": character.realm_or_level or "",
            "physical_state": character.physical_state or "",
            "mental_state": character.mental_state or "",
            "current_goal": character.current_goal or "",
            "active_conflict": character.active_conflict or "",
            "abilities_state": character.abilities_state or "",
            "items_or_assets": character.items_or_assets or "",
        },
        "profile": profile,
        "ai_config": {
            "tone_style": config.tone_style or "",
            "catchphrases": _json_list(config.catchphrases),
            "verbosity": config.verbosity or "",
            "emotion_tendency": config.emotion_tendency or "",
            "model_override": config.model_override or "",
            "custom_system_prompt": config.custom_system_prompt or "",
        }
        if config
        else None,
    }
    if db is not None:
        payload["relationships"] = _relationship_payloads(db, character)
    reveal_chapter = profile.get("reveal_chapter")
    if (
        isinstance(target_chapter_number, int)
        and not isinstance(target_chapter_number, bool)
        and target_chapter_number > 0
        and isinstance(reveal_chapter, int)
        and not isinstance(reveal_chapter, bool)
        and reveal_chapter > target_chapter_number
    ):
        payload.update(
            {
                "aliases": [],
                "age": "",
                "appearance": "",
                "personality": "",
                "background": "",
                "abilities": [],
                "state": {key: "" for key in payload["state"]},
                "profile": {
                    field: reveal_chapter if field == "reveal_chapter" else ""
                    for field in CHARACTER_PROFILE_FIELDS
                },
                "ai_config": None,
                "relationships": [],
                "disclosure": {
                    "status": "withheld_until_chapter",
                    "target_chapter_number": target_chapter_number,
                    "reveal_chapter": reveal_chapter,
                },
            }
        )
    return payload


def character_archive_text(
    character: Character,
    *,
    db: Session | None = None,
    target_chapter_number: int | None = None,
) -> str:
    """Serialize the canonical card deterministically for prompts and hashes."""
    return json.dumps(
        character_archive_payload(
            character,
            db=db,
            target_chapter_number=target_chapter_number,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "CHARACTER_PROFILE_FIELDS",
    "character_archive_payload",
    "character_archive_text",
]
