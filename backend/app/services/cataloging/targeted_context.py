"""Build targeted context for the second cataloging stage."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    Chapter,
    ChapterSummary,
    Character,
    CharacterRelationship,
    OutlineNode,
    WorldbuildingEntry,
)
from .facts import extract_fact_terms, facts_text
from .context import ordered_chapters
from .constants import (
    CATALOGING_CHARACTER_INDEX_LIMIT,
    CATALOGING_CONTEXT_FACT_MATCH_LIMIT,
    CATALOGING_RELEVANT_CHARACTER_LIMIT,
    CATALOGING_RELEVANT_WORLDBUILDING_LIMIT,
    CATALOGING_WORLDBUILDING_INDEX_LIMIT,
)


def build_targeted_context(db: Session, project_id: str, chapter: Chapter, facts: list[dict[str, Any]]) -> dict:
    terms = extract_fact_terms(facts)
    fact_text = facts_text(facts, limit=CATALOGING_CONTEXT_FACT_MATCH_LIMIT)
    characters = _load_relevant_characters(db, project_id, terms["names"], fact_text)
    world_entries = _load_relevant_worldbuilding(db, project_id, terms["titles"] | terms["keywords"], fact_text)
    chapters = ordered_chapters(db, project_id)
    index = next((idx for idx, item in enumerate(chapters) if item.id == chapter.id), 0)
    return {
        "current_chapter": {
            "index": index + 1,
            "total": len(chapters),
            "title": chapter.title,
        },
        "recent_chapter_summaries": _recent_summaries(db, chapters, index),
        "character_name_index": [
            {
                "name": item.name,
                "age": item.age,
                "role_type": item.role_type,
                "life_status": item.life_status,
                "aliases": [alias.alias for alias in (item.aliases or []) if alias.alias],
            }
            for item in _load_character_index(db, project_id)
        ],
        "relevant_characters": [_character_context(item) for item in characters],
        "relevant_relationships": _relationship_context(db, project_id, characters),
        "worldbuilding_title_index": [
            {"dimension": item.dimension, "title": item.title}
            for item in _load_worldbuilding_index(db, project_id)
        ],
        "relevant_worldbuilding": [_worldbuilding_context(item) for item in world_entries],
        "nearby_outline_nodes": _nearby_outline_nodes(db, project_id, index),
        "lookup_terms": {
            "names": sorted(terms["names"]),
            "titles": sorted(terms["titles"]),
            "keywords": sorted(terms["keywords"])[:80],
        },
    }


def _load_character_index(db: Session, project_id: str) -> list[Character]:
    return (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.updated_at.desc())
        .limit(CATALOGING_CHARACTER_INDEX_LIMIT)
        .all()
    )


def _load_worldbuilding_index(db: Session, project_id: str) -> list[WorldbuildingEntry]:
    return (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.updated_at.desc())
        .limit(CATALOGING_WORLDBUILDING_INDEX_LIMIT)
        .all()
    )


def _load_relevant_characters(db: Session, project_id: str, names: set[str], fact_text: str) -> list[Character]:
    characters = _load_character_index(db, project_id)
    selected: list[Character] = []
    for character in characters:
        if _character_matches(character, names, fact_text):
            selected.append(character)
        if len(selected) >= CATALOGING_RELEVANT_CHARACTER_LIMIT:
            break
    return selected


def _character_matches(character: Character, names: set[str], fact_text: str) -> bool:
    name = character.name or ""
    if name in names or (name and name in fact_text):
        return True
    for alias in character.aliases or []:
        alias_text = alias.alias or ""
        if alias_text in names or (alias_text and alias_text in fact_text):
            return True
    for term in names:
        if term and (term in name or name in term):
            return True
    return False


def _load_relevant_worldbuilding(
    db: Session,
    project_id: str,
    terms: set[str],
    fact_text: str,
) -> list[WorldbuildingEntry]:
    entries = _load_worldbuilding_index(db, project_id)
    selected: list[WorldbuildingEntry] = []
    for entry in entries:
        if _worldbuilding_matches(entry, terms, fact_text):
            selected.append(entry)
        if len(selected) >= CATALOGING_RELEVANT_WORLDBUILDING_LIMIT:
            break
    return selected


def _worldbuilding_matches(entry: WorldbuildingEntry, terms: set[str], fact_text: str) -> bool:
    title = entry.title or ""
    content = entry.content or ""
    if title and title in fact_text:
        return True
    for term in terms:
        if not term:
            continue
        if term in title or title in term:
            return True
        if len(term) >= 3 and term in content:
            return True
    return False


def _recent_summaries(db: Session, chapters: list[Chapter], index: int) -> list[dict]:
    summaries = []
    for item in chapters[max(0, index - 3):index]:
        summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == item.id).first()
        if summary:
            summaries.append({
                "title": item.title,
                "summary": _clip(summary.summary_text, 420),
                "key_events": _parse_list(summary.key_events)[:5],
            })
    return summaries


def _nearby_outline_nodes(db: Session, project_id: str, chapter_index: int) -> list[dict]:
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .limit(260)
        .all()
    )
    return [
        {
            "id": item.id,
            "title": item.title,
            "node_type": item.node_type,
            "parent_id": item.parent_id,
            "status": item.status,
            "summary": _clip(item.summary, 260),
            "actual_summary": _clip(item.actual_summary, 260),
            "planned_summary": _clip(item.planned_summary, 260),
        }
        for item in nodes[max(0, chapter_index - 5): chapter_index + 8]
    ]


def _character_context(character: Character) -> dict:
    config = character.ai_config
    recent_events = sorted(
        character.timeline_events or [],
        key=lambda event: event.created_at,
        reverse=True,
    )[:4]
    return {
        "id": character.id,
        "name": character.name,
        "aliases": [
            {
                "alias": alias.alias,
                "alias_type": alias.alias_type,
                "description": _clip(alias.description, 300),
            }
            for alias in (character.aliases or [])
        ],
        "role_type": character.role_type,
        "age": character.age,
        "appearance": _clip(character.appearance, 360),
        "personality": _clip(character.personality, 480),
        "background": _clip(character.background, 720),
        "abilities": _parse_list(character.abilities)[:12],
        "life_status": character.life_status,
        "current_location": character.current_location,
        "realm_or_level": character.realm_or_level,
        "physical_state": _clip(character.physical_state, 260),
        "mental_state": _clip(character.mental_state, 260),
        "current_goal": _clip(character.current_goal, 260),
        "active_conflict": _clip(character.active_conflict, 260),
        "abilities_state": _clip(character.abilities_state, 260),
        "items_or_assets": _clip(character.items_or_assets, 260),
        "recent_timeline": [
            {
                "event_type": event.event_type,
                "event_description": _clip(event.event_description, 260),
                "emotional_state_change": _clip(event.emotional_state_change, 160),
            }
            for event in recent_events
        ],
        "ai_style": {
            "tone_style": config.tone_style,
            "emotion_tendency": config.emotion_tendency,
            "catchphrases": _parse_list(config.catchphrases)[:6],
            "custom_system_prompt": _clip(config.custom_system_prompt, 420),
        } if config else None,
    }


def _relationship_context(db: Session, project_id: str, characters: list[Character]) -> list[dict]:
    ids = {character.id for character in characters}
    if not ids:
        return []
    relationships = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project_id)
        .filter(
            (CharacterRelationship.character_a_id.in_(ids))
            | (CharacterRelationship.character_b_id.in_(ids))
        )
        .limit(40)
        .all()
    )
    by_id = {character.id: character.name for character in _load_character_index(db, project_id)}
    return [
        {
            "source_name": by_id.get(item.character_a_id, item.character_a_id),
            "target_name": by_id.get(item.character_b_id, item.character_b_id),
            "relationship_type": item.relationship_type,
            "description": _clip(item.description, 260),
        }
        for item in relationships
    ]


def _worldbuilding_context(entry: WorldbuildingEntry) -> dict:
    recent_events = sorted(
        entry.timeline_events or [],
        key=lambda event: event.created_at,
        reverse=True,
    )[:3]
    return {
        "id": entry.id,
        "dimension": entry.dimension,
        "title": entry.title,
        "status": entry.status,
        "content": _clip(entry.content, 820),
        "recent_timeline": [
            {
                "event_type": event.event_type,
                "event_description": _clip(event.event_description, 260),
                "evidence": _clip(event.evidence, 160),
            }
            for event in recent_events
        ],
    }


def _clip(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


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
