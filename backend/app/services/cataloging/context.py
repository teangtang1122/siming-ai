"""Context builders for per-chapter cataloging."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ...database.models import Chapter, Character, ChapterSummary, OutlineNode, WorldbuildingEntry
from ...services.outline_service import load_outline_nodes, outline_sort_context


def ordered_chapters(db: Session, project_id: str, chapter_ids: list[str] | None = None) -> list[Chapter]:
    outline_context = outline_sort_context(load_outline_nodes(db, project_id))
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    chapters = query.all()
    by_id = {chapter.id: chapter for chapter in chapters}
    if chapter_ids:
        return [by_id[item] for item in chapter_ids if item in by_id]

    def sort_key(chapter: Chapter):
        outline_key = outline_context["sort_keys"].get(chapter.outline_node_id)
        if outline_key is None:
            return (1, (999999,), chapter.created_at)
        return (0, outline_key, chapter.created_at)

    return sorted(chapters, key=sort_key)


def build_light_context(db: Session, project_id: str, chapter: Chapter) -> dict:
    chapters = ordered_chapters(db, project_id)
    index = next((idx for idx, item in enumerate(chapters) if item.id == chapter.id), 0)
    recent = chapters[max(0, index - 5):index]
    recent_summaries = []
    for item in recent:
        summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == item.id).first()
        if summary:
            recent_summaries.append({
                "title": item.title,
                "summary": summary.summary_text[:600],
                "key_events": _parse_list(summary.key_events)[:6],
            })

    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.updated_at.desc())
        .limit(120)
        .all()
    )
    world_entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.updated_at.desc())
        .limit(120)
        .all()
    )
    outline_nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .limit(160)
        .all()
    )
    previous_states = []
    for character in characters[:30]:
        state = {
            "name": character.name,
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
        }
        if any(value for key, value in state.items() if key != "name"):
            previous_states.append(state)

    return {
        "current_chapter": {
            "index": index + 1,
            "total": len(chapters),
            "title": chapter.title,
        },
        "recent_chapter_summaries": recent_summaries,
        "character_index": [
            {
                "name": item.name,
                "age": item.age,
                "role_type": item.role_type,
                "life_status": item.life_status,
                "current_location": item.current_location,
                "realm_or_level": item.realm_or_level,
                "aliases": [alias.alias for alias in (item.aliases or []) if alias.alias],
            }
            for item in characters
        ],
        "character_details": [_character_detail(item) for item in characters[:40]],
        "worldbuilding_index": [
            {"dimension": item.dimension, "title": item.title}
            for item in world_entries
        ],
        "worldbuilding_details": [_worldbuilding_detail(item) for item in world_entries[:50]],
        "nearby_outline_nodes": [
            {
                "title": item.title,
                "node_type": item.node_type,
                "summary": _clip(item.summary, 420),
                "actual_summary": _clip(item.actual_summary, 420),
                "planned_summary": _clip(item.planned_summary, 420),
            }
            for item in outline_nodes[max(0, index - 8): index + 12]
        ],
        "previous_character_states": previous_states,
    }


def _character_detail(character: Character) -> dict:
    config = character.ai_config
    return {
        "name": character.name,
        "aliases": [alias.alias for alias in (character.aliases or []) if alias.alias],
        "role_type": character.role_type,
        "age": character.age,
        "appearance": _clip(character.appearance, 360),
        "personality": _clip(character.personality, 420),
        "background": _clip(character.background, 560),
        "abilities": _parse_list(character.abilities)[:12],
        "life_status": character.life_status,
        "current_location": character.current_location,
        "realm_or_level": character.realm_or_level,
        "physical_state": _clip(character.physical_state, 320),
        "mental_state": _clip(character.mental_state, 320),
        "current_goal": _clip(character.current_goal, 320),
        "active_conflict": _clip(character.active_conflict, 320),
        "abilities_state": _clip(character.abilities_state, 320),
        "items_or_assets": _clip(character.items_or_assets, 320),
        "ai_style": {
            "tone_style": config.tone_style,
            "emotion_tendency": config.emotion_tendency,
            "catchphrases": _parse_list(config.catchphrases)[:8],
            "custom_system_prompt": _clip(config.custom_system_prompt, 700),
        } if config else None,
    }


def _worldbuilding_detail(entry: WorldbuildingEntry) -> dict:
    recent_events = sorted(
        entry.timeline_events or [],
        key=lambda event: event.created_at,
        reverse=True,
    )[:4]
    return {
        "dimension": entry.dimension,
        "title": entry.title,
        "status": entry.status,
        "content": _clip(entry.content, 1000),
        "recent_timeline": [
            {
                "event_type": event.event_type,
                "event_description": _clip(event.event_description, 360),
                "evidence": _clip(event.evidence, 220),
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
