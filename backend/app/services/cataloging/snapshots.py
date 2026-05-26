"""Snapshot helpers for cataloging writes."""
from __future__ import annotations

import json
from typing import Any

from ...database.models import Character, Chapter, OutlineNode, WorldbuildingEntry


def character_snapshot(character: Character | None) -> dict | None:
    if not character:
        return None
    abilities: list[str] = []
    if character.abilities:
        try:
            parsed = json.loads(character.abilities)
            abilities = parsed if isinstance(parsed, list) else []
        except Exception:
            abilities = []
    return {
        "id": character.id,
        "name": character.name,
        "aliases": [item.alias for item in (character.aliases or []) if item.alias],
        "appearance": character.appearance,
        "personality": character.personality,
        "background": character.background,
        "abilities": abilities,
        "role_type": character.role_type,
        "life_status": character.life_status,
        "current_location": character.current_location,
        "realm_or_level": character.realm_or_level,
        "physical_state": character.physical_state,
        "mental_state": character.mental_state,
        "current_goal": character.current_goal,
        "active_conflict": character.active_conflict,
        "abilities_state": character.abilities_state,
        "items_or_assets": character.items_or_assets,
        "ai_config": {
            "tone_style": character.ai_config.tone_style,
            "catchphrases": _parse_list(character.ai_config.catchphrases),
            "verbosity": character.ai_config.verbosity,
            "emotion_tendency": character.ai_config.emotion_tendency,
            "custom_system_prompt": character.ai_config.custom_system_prompt,
        } if character.ai_config else None,
    }


def worldbuilding_snapshot(entry: WorldbuildingEntry | None) -> dict | None:
    if not entry:
        return None
    return {
        "id": entry.id,
        "dimension": entry.dimension,
        "title": entry.title,
        "content": entry.content,
        "status": entry.status,
        "confidence": entry.confidence,
    }


def outline_snapshot(node: OutlineNode | None) -> dict | None:
    if not node:
        return None
    return {
        "id": node.id,
        "title": node.title,
        "node_type": node.node_type,
        "parent_id": node.parent_id,
        "summary": node.summary,
        "status": node.status,
        "source_chapter_id": node.source_chapter_id,
        "actual_summary": node.actual_summary,
        "planned_summary": node.planned_summary,
    }


def chapter_change_title(chapter: Chapter, summary: Any) -> str:
    detail = str(summary or "").strip()
    if len(detail) > 80:
        detail = detail[:80] + "..."
    return f"《{chapter.title}》：{detail or '信息更新'}"


def _parse_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        return []
    return []
