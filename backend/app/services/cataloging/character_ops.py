"""Character cataloging writes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    CharacterTimeline,
    CharacterVersion,
)
from .background_compactor import merge_background
from .links import link_chapter_character
from .lookups import find_character_by_name_or_id
from .merge import merge_json_list, merge_short_text, merge_text
from .snapshots import character_snapshot, chapter_change_title


CHARACTER_TEXT_FIELDS = ["appearance", "personality", "background", "role_type"]
CHARACTER_STATE_FIELDS = [
    "life_status",
    "current_location",
    "realm_or_level",
    "physical_state",
    "mental_state",
    "current_goal",
    "active_conflict",
    "abilities_state",
    "items_or_assets",
]

STATE_FIELD_LIMITS = {
    "life_status": 50,
    "current_location": 200,
    "realm_or_level": 200,
    "physical_state": 2000,
    "mental_state": 2000,
    "current_goal": 2000,
    "active_conflict": 2000,
    "abilities_state": 2000,
    "items_or_assets": 2000,
}

CHARACTER_CHANGE_LABELS = {
    "appearance": "外貌",
    "personality": "性格",
    "background": "背景",
    "role_type": "角色定位",
    "abilities": "能力",
    "custom_system_prompt": "角色扮演提示词",
    "tone_style": "语气风格",
    "catchphrases": "口头禅",
    "verbosity": "表达详略",
    "emotion_tendency": "情绪倾向",
    "life_status": "生死状态",
    "current_location": "当前位置",
    "realm_or_level": "境界",
    "physical_state": "身体状态",
    "mental_state": "心理状态",
    "current_goal": "当前目标",
    "active_conflict": "当前冲突",
    "abilities_state": "能力状态",
    "items_or_assets": "持有物/资源",
}


def apply_character_create(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("角色名为空")
    character = find_character_by_name_or_id(db, chapter.project_id, name)
    old = character_snapshot(character) if character else None
    if not character:
        character = Character(project_id=chapter.project_id, name=name[:100], current_version=1, is_evolution_tracked=True)
        db.add(character)
        db.flush()
    fill_character_fields(db, character, chapter, payload)
    ensure_character_version(db, character, chapter, payload, old is None)
    link_chapter_character(db, chapter, character, str(payload.get("role_in_scene") or "出场"))
    return _character_result(character, old, f"角色已写入: {character.name}")


def apply_character_update(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    character = find_character_by_name_or_id(db, chapter.project_id, payload.get("id") or payload.get("name"))
    if not character:
        return apply_character_create(db, candidate, chapter, payload)
    old = character_snapshot(character)
    fill_character_fields(db, character, chapter, payload)
    ensure_character_version(db, character, chapter, payload, False)
    link_chapter_character(db, chapter, character, str(payload.get("role_in_scene") or "提及"))
    return _character_result(character, old, f"角色已更新: {character.name}")


def apply_character_state(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    character = find_character_by_name_or_id(db, chapter.project_id, payload.get("id") or payload.get("name"))
    if not character:
        character = Character(project_id=chapter.project_id, name=str(payload.get("name") or "未命名角色")[:100], current_version=1)
        db.add(character)
        db.flush()
    old = character_snapshot(character)
    changed = False
    for field in CHARACTER_STATE_FIELDS:
        if field in payload and payload.get(field) not in (None, ""):
            value = _replacement_text(payload.get(field), STATE_FIELD_LIMITS.get(field, 2000))
            if getattr(character, field) != value:
                setattr(character, field, value)
                changed = True
    character.last_seen_chapter_id = chapter.id
    character.last_updated_chapter_id = chapter.id
    character.updated_at = datetime.utcnow()
    if changed:
        ensure_character_version(db, character, chapter, payload, False)
    link_chapter_character(db, chapter, character, "状态变化")
    return _character_result(character, old, f"角色状态已更新: {character.name}")


def apply_character_timeline(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    character = find_character_by_name_or_id(db, chapter.project_id, payload.get("id") or payload.get("name"))
    if not character:
        raise ValueError("时间线关联角色不存在")
    event = CharacterTimeline(
        character_id=character.id,
        chapter_id=chapter.id,
        event_description=str(payload.get("event_description") or payload.get("description") or "")[:4000],
        event_type=str(payload.get("event_type") or "key_event")[:50],
        emotional_state_change=str(payload.get("emotional_state_change") or "")[:2000],
        sort_order=int(payload.get("sort_order") or 0),
    )
    if not event.event_description:
        raise ValueError("角色时间线事件为空")
    db.add(event)
    link_chapter_character(db, chapter, character, "时间线")
    return {
        "target_type": "character_timeline",
        "target_id": event.id,
        "old_value": None,
        "new_value": payload,
        "detail": f"角色时间线已写入: {character.name}",
    }


def apply_character_relationship(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    source_name = str(payload.get("source_name") or payload.get("character_a") or "").strip()
    target_name = str(payload.get("target_name") or payload.get("character_b") or "").strip()
    if not source_name or not target_name:
        raise ValueError("角色关系缺少 source_name 或 target_name")
    if source_name == target_name:
        raise ValueError("角色关系不能指向同一角色")
    source = find_character_by_name_or_id(db, chapter.project_id, source_name)
    if not source:
        source = Character(project_id=chapter.project_id, name=source_name[:100], current_version=1)
        db.add(source)
        db.flush()
    target = find_character_by_name_or_id(db, chapter.project_id, target_name)
    if not target:
        target = Character(project_id=chapter.project_id, name=target_name[:100], current_version=1)
        db.add(target)
        db.flush()

    relationship_type = str(payload.get("relationship_type") or "关联")[:100]
    description = str(payload.get("description") or payload.get("evidence") or candidate.evidence or "")[:4000]
    relationship = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == chapter.project_id,
            CharacterRelationship.character_a_id == source.id,
            CharacterRelationship.character_b_id == target.id,
            CharacterRelationship.relationship_type == relationship_type,
        )
        .first()
    )
    old = None
    if relationship:
        old = {
            "relationship_type": relationship.relationship_type,
            "description": relationship.description,
        }
        if description:
            relationship.description = merge_short_text(relationship.description, description, chapter, limit=4000)
    else:
        relationship = CharacterRelationship(
            project_id=chapter.project_id,
            character_a_id=source.id,
            character_b_id=target.id,
            relationship_type=relationship_type,
            description=description,
        )
        db.add(relationship)
        db.flush()

    link_chapter_character(db, chapter, source, f"关系：{target.name} / {relationship_type}")
    link_chapter_character(db, chapter, target, f"关系：{source.name} / {relationship_type}")
    return {
        "target_type": "character_relationship",
        "target_id": relationship.id,
        "old_value": old,
        "new_value": {
            "source_name": source.name,
            "target_name": target.name,
            "relationship_type": relationship.relationship_type,
            "description": relationship.description,
        },
        "detail": f"角色关系已写入: {source.name} -> {target.name}",
    }


def fill_character_fields(db: Session, character: Character, chapter: Chapter, payload: dict[str, Any]) -> None:
    for field in CHARACTER_TEXT_FIELDS:
        if field in payload and payload.get(field) not in (None, ""):
            if field == "role_type":
                if not character.role_type or character.role_type == "other":
                    character.role_type = str(payload.get(field))[:100]
            elif field == "background":
                character.background = merge_background(character.background, payload.get(field), chapter)
            else:
                setattr(character, field, merge_text(getattr(character, field), payload.get(field), chapter, limit=8000))
    if isinstance(payload.get("abilities"), list):
        character.abilities = merge_json_list(character.abilities, payload["abilities"])
    for field in CHARACTER_STATE_FIELDS:
        if field in payload and payload.get(field) not in (None, ""):
            setattr(character, field, _replacement_text(payload.get(field), STATE_FIELD_LIMITS.get(field, 2000)))
    character.last_seen_chapter_id = chapter.id
    character.last_updated_chapter_id = chapter.id
    character.updated_at = datetime.utcnow()
    _update_ai_config(db, character, payload)


def ensure_character_version(
    db: Session,
    character: Character,
    chapter: Chapter,
    payload: dict[str, Any],
    is_create: bool,
) -> None:
    if not is_create:
        character.current_version = (character.current_version or 1) + 1
    db.add(CharacterVersion(
        character_id=character.id,
        version_number=character.current_version or 1,
        snapshot_data=json.dumps(character_snapshot(character), ensure_ascii=False),
        change_summary=chapter_change_title(
            chapter,
            payload.get("change_summary") or payload.get("event_description") or _character_change_summary(payload, is_create),
        ),
        source_chapter_id=chapter.id,
    ))


def _update_ai_config(db: Session, character: Character, payload: dict[str, Any]) -> None:
    has_config_fields = any(
        field in payload and payload.get(field) not in (None, "")
        for field in ["custom_system_prompt", "tone_style", "catchphrases", "verbosity", "emotion_tendency"]
    )
    if not has_config_fields:
        return
    config = character.ai_config or db.query(CharacterAIConfig).filter(CharacterAIConfig.character_id == character.id).first()
    if not config:
        config = CharacterAIConfig(character_id=character.id)
        db.add(config)
    character.ai_config = config
    prompt = str(payload.get("custom_system_prompt") or "").strip()
    if prompt:
        config.custom_system_prompt = prompt[:12000]
    if payload.get("tone_style"):
        config.tone_style = str(payload.get("tone_style"))[:100]
    if payload.get("verbosity"):
        config.verbosity = str(payload.get("verbosity"))[:50]
    if payload.get("emotion_tendency"):
        config.emotion_tendency = str(payload.get("emotion_tendency"))[:100]
    if isinstance(payload.get("catchphrases"), list):
        config.catchphrases = json.dumps([str(item) for item in payload["catchphrases"]], ensure_ascii=False)


def _replacement_text(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _character_change_summary(payload: dict[str, Any], is_create: bool) -> str:
    action = "创建角色档案" if is_create else "更新角色档案"
    changed: list[str] = []
    for field, label in CHARACTER_CHANGE_LABELS.items():
        if field in payload and payload.get(field) not in (None, "", []):
            changed.append(label)
    if not changed:
        return action
    detail = "、".join(dict.fromkeys(changed))
    name = str(payload.get("name") or "").strip()
    prefix = f"{name}：" if name else ""
    return f"{prefix}{action}（{detail}）"


def _character_result(character: Character, old: dict | None, detail: str) -> dict:
    return {
        "target_type": "character",
        "target_id": character.id,
        "old_value": old,
        "new_value": character_snapshot(character),
        "detail": detail,
    }
