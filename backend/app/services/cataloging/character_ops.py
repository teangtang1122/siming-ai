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
from ..character_role_types import normalize_character_role_type
from ..character_relationships import collapse_directed_relationship_pair
from ..planned_character_links import resolve_planned_outline_character_links
from ..story_granularity import CHARACTER_PROFILE_FIELDS, CHARACTER_STATE_FIELDS
from .alias_ops import ensure_character_alias
from .character_targets import (
    validate_character_profile_target,
    validate_character_state_target,
)
from .lookups import find_character_by_name_or_id
from .merge import merge_json_list, merge_short_text, merge_text
from .snapshots import chapter_change_title, character_snapshot

CHARACTER_TEXT_FIELDS = ["personality", "background", "role_type"]
AI_CONFIG_FIELDS = (
    "custom_system_prompt",
    "tone_style",
    "catchphrases",
    "verbosity",
    "emotion_tendency",
)

STATE_FIELD_LIMITS = {
    "appearance": 8000,
    "age": 100,
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

PLACEHOLDER_CHARACTER_NAMES = {"未命名", "未命名角色", "未命名主角", "未知", "无名", "角色名", "某人"}

CHARACTER_CHANGE_LABELS = {
    "appearance": "外貌",
    "personality": "性格",
    "background": "背景",
    "role_type": "角色定位",
    "abilities": "能力",
    "aliases": "别名",
    "custom_system_prompt": "角色扮演提示词",
    "tone_style": "语气风格",
    "catchphrases": "口头禅",
    "verbosity": "表达详略",
    "emotion_tendency": "情绪倾向",
    "age": "年龄/时间状态",
    "life_status": "生死状态",
    "current_location": "当前位置",
    "realm_or_level": "境界",
    "physical_state": "身体状态",
    "mental_state": "心理状态",
    "current_goal": "当前目标",
    "active_conflict": "当前冲突",
    "abilities_state": "能力状态",
    "items_or_assets": "持有物/资源",
    "profile": "稳定写作锁",
}


def apply_character_create(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    validate_character_profile_target(db, chapter.project_id, "character_create", payload)
    name, aliases = _identity_from_payload(payload)
    if not name or _is_placeholder_character_name(name):
        raise ValueError("角色名为空")
    character = Character(project_id=chapter.project_id, name=name[:100], current_version=1, is_evolution_tracked=True)
    db.add(character)
    db.flush()
    fill_character_fields(db, character, chapter, payload)
    _write_character_aliases(db, character, chapter, aliases)
    resolve_planned_outline_character_links(db, character)
    ensure_character_version(db, character, chapter, payload, True)
    return _character_result(character, None, f"角色已写入: {character.name}")


def apply_character_update(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    character = validate_character_profile_target(db, chapter.project_id, "character_update", payload)
    name, aliases = _identity_from_payload(payload)
    assert character is not None
    old = character_snapshot(character)
    payload, preserved_fields = _prepare_reconciled_character_update(
        db, character, payload
    )
    if name and character.name != name:
        aliases.append(character.name)
        character.name = name[:100]
    fill_character_fields(db, character, chapter, payload)
    _write_character_aliases(db, character, chapter, aliases)
    if character_snapshot(character) != old:
        ensure_character_version(db, character, chapter, payload, False)
    result = _character_result(character, old, f"角色已更新: {character.name}")
    if preserved_fields:
        result["review_warning"] = (
            f"角色“{character.name}”的旧版建档字段已被作者或后续章节修改，"
            "系统保留当前值并跳过自动覆盖："
            + "、".join(preserved_fields)
        )
    return result


def apply_character_state(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    validate_character_state_target(
        db,
        chapter.project_id,
        "character_state_update",
        payload,
        chapter_content=str(chapter.content or ""),
    )
    name, aliases = _identity_from_payload(payload)
    target_id = payload.get("id")
    character = db.query(Character).filter(
        Character.project_id == chapter.project_id,
        Character.id == target_id if target_id is not None else Character.name == name,
    ).first()
    if not character:
        raise ValueError("角色状态更新引用的角色不存在；必须先生成 character_create 或 character_update")
    old = character_snapshot(character)
    changed = False
    can_advance = _chapter_can_advance_character_state(db, character, chapter)
    if can_advance:
        for field in CHARACTER_STATE_FIELDS:
            if field in payload and payload.get(field) not in (None, ""):
                value = _replacement_text(payload.get(field), STATE_FIELD_LIMITS.get(field, 2000))
                if getattr(character, field) != value:
                    setattr(character, field, value)
                    changed = True
        character.last_seen_chapter_id = chapter.id
        character.last_updated_chapter_id = chapter.id
    character.updated_at = datetime.utcnow()
    _write_character_aliases(db, character, chapter, aliases)
    if changed:
        ensure_character_version(db, character, chapter, payload, False)
    return _character_result(character, old, f"角色状态已更新: {character.name}")


def apply_character_timeline(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    character = find_character_by_name_or_id(db, chapter.project_id, payload.get("id") or payload.get("name"))
    if not character:
        raise ValueError("时间线关联角色不存在")
    description = str(payload.get("event_description") or payload.get("description") or "")[:4000]
    if not description:
        raise ValueError("角色时间线事件为空")
    event_type = str(payload.get("event_type") or "key_event")[:50]
    sort_order = int(payload.get("sort_order") or candidate.sort_order or 0)
    preferred_id = str(payload.get("_cataloging_target_id") or "").strip()
    event = (
        db.query(CharacterTimeline)
        .filter(
            CharacterTimeline.id == preferred_id,
            CharacterTimeline.chapter_id == chapter.id,
            CharacterTimeline.character_id == character.id,
        )
        .first()
        if preferred_id
        else None
    )
    if not event:
        event = (
            db.query(CharacterTimeline)
            .filter(
                CharacterTimeline.character_id == character.id,
                CharacterTimeline.chapter_id == chapter.id,
                CharacterTimeline.event_type == event_type,
                CharacterTimeline.sort_order == sort_order,
            )
            .order_by(CharacterTimeline.created_at.asc())
            .first()
        )
    old = None
    if event:
        old = {
            "event_description": event.event_description,
            "event_type": event.event_type,
            "emotional_state_change": event.emotional_state_change,
            "sort_order": event.sort_order,
        }
        event.event_description = description
        event.event_type = event_type
        event.emotional_state_change = str(payload.get("emotional_state_change") or "")[:2000]
        event.sort_order = sort_order
    else:
        event = CharacterTimeline(
            character_id=character.id,
            chapter_id=chapter.id,
            event_description=description,
            event_type=event_type,
            emotional_state_change=str(payload.get("emotional_state_change") or "")[:2000],
            sort_order=sort_order,
        )
        db.add(event)
    db.flush()
    return {
        "target_type": "character_timeline",
        "target_id": event.id,
        "old_value": old,
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
        raise ValueError(f"角色关系来源角色不存在：{source_name}；必须先生成角色档案候选")
    target = find_character_by_name_or_id(db, chapter.project_id, target_name)
    if not target:
        raise ValueError(f"角色关系目标角色不存在：{target_name}；必须先生成角色档案候选")

    relationship_type = str(payload.get("relationship_type") or "关联")[:100]
    description = str(payload.get("description") or payload.get("evidence") or candidate.evidence or "")[:4000]
    preferred_id = str(payload.get("_cataloging_target_id") or "").strip()
    relationship, removed_relationship_ids = collapse_directed_relationship_pair(
        db,
        chapter.project_id,
        source.id,
        target.id,
        preferred_id=preferred_id or None,
    )
    old = None
    if relationship:
        old = {
            "relationship_type": relationship.relationship_type,
            "description": relationship.description,
        }
        relationship.character_a_id = source.id
        relationship.character_b_id = target.id
        relationship.relationship_type = relationship_type
        if description:
            previous = payload.get("_cataloging_previous_payload")
            prior_description = previous.get("description") if isinstance(previous, dict) else None
            relationship.description = _replace_previous_text(
                relationship.description,
                prior_description,
                description,
                chapter,
                limit=4000,
                short=True,
            )
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
        "deduplicated_relationship_ids": removed_relationship_ids,
    }


def fill_character_fields(db: Session, character: Character, chapter: Chapter, payload: dict[str, Any]) -> None:
    for field in CHARACTER_TEXT_FIELDS:
        if field in payload and payload.get(field) not in (None, ""):
            if field == "role_type":
                if not character.role_type or character.role_type == "other":
                    character.role_type = normalize_character_role_type(payload.get(field))
            elif field == "background":
                # character_update defines background as a complete rewritten
                # field.  Appending that cumulative profile duplicates earlier
                # chapter history whenever the model paraphrases old facts.
                character.background = _replacement_text(payload.get(field), 12000)
            else:
                # personality follows the same replacement contract.  Per
                # chapter changes belong in character_state_update/timeline.
                setattr(character, field, _replacement_text(payload.get(field), 8000))
    if isinstance(payload.get("abilities"), list):
        character.abilities = merge_json_list(character.abilities, payload["abilities"])
    _write_character_aliases(db, character, chapter, _aliases_from_payload(payload, character.name))
    if _chapter_can_advance_character_state(db, character, chapter):
        for field in CHARACTER_STATE_FIELDS:
            if field in payload and payload.get(field) not in (None, ""):
                setattr(
                    character,
                    field,
                    _replacement_text(
                        payload.get(field),
                        STATE_FIELD_LIMITS.get(field, 2000),
                    ),
                )
        character.last_seen_chapter_id = chapter.id
        character.last_updated_chapter_id = chapter.id
    character.updated_at = datetime.utcnow()
    _update_character_profile(character, payload)
    _update_ai_config(db, character, payload)


def _prepare_reconciled_character_update(
    db: Session,
    character: Character,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Replace the prior chapter contribution without overwriting later edits.

    Apply logs provide the exact before/after snapshots for the previous saved
    version.  When the live value still contains that exact projection it is
    safe to remove it before merging the new one.  A mismatch means an author
    or later chapter has changed the field, so the current value wins and the
    run receives an explicit review warning.
    """

    previous_payload = payload.get("_cataloging_previous_payload")
    previous_old = payload.get("_cataloging_previous_old_snapshot")
    previous_new = payload.get("_cataloging_previous_new_snapshot")
    if not all(
        isinstance(value, dict)
        for value in (previous_payload, previous_old, previous_new)
    ):
        return payload, []

    prepared = dict(payload)
    if isinstance(prepared.get("ai_config"), dict):
        prepared["ai_config"] = dict(prepared["ai_config"])
    preserved: list[str] = []

    for field in ("personality", "background"):
        if field not in prepared or field not in previous_payload:
            continue
        current_value = getattr(character, field)
        old_value = previous_old.get(field)
        new_value = previous_new.get(field)
        if current_value == new_value:
            setattr(character, field, old_value)
            continue
        # Any mismatch belongs to an author edit or a later chapter.  A full
        # replacement from an older revised chapter must never erase it.
        prepared.pop(field, None)
        preserved.append(field)

    profile_keys = {"profile", "profile_json", *CHARACTER_PROFILE_FIELDS}
    incoming_has_profile = any(key in prepared for key in profile_keys)
    previous_has_profile = any(key in previous_payload for key in profile_keys)
    if (
        incoming_has_profile
        and previous_has_profile
        and character.profile_json != previous_new.get("profile")
    ):
        for key in profile_keys:
            prepared.pop(key, None)
        preserved.append("profile")

    config = character.ai_config or db.query(CharacterAIConfig).filter(
        CharacterAIConfig.character_id == character.id
    ).first()
    for field in AI_CONFIG_FIELDS:
        incoming_present, _incoming_value = _config_field(prepared, field)
        previous_present, previous_value = _config_field(previous_payload, field)
        if not incoming_present or not previous_present:
            continue
        current_value = getattr(config, field, None) if config is not None else None
        if field == "catchphrases":
            current_value = _json_list_value(current_value)
            previous_value = _json_list_value(previous_value)
        if current_value == previous_value:
            continue
        prepared.pop(field, None)
        nested = prepared.get("ai_config")
        if isinstance(nested, dict):
            nested.pop(field, None)
        preserved.append(field)

    return prepared, list(dict.fromkeys(preserved))


def _config_field(payload: dict[str, Any], field: str) -> tuple[bool, Any]:
    if field in payload:
        return True, payload.get(field)
    nested = payload.get("ai_config")
    if isinstance(nested, dict) and field in nested:
        return True, nested.get(field)
    return False, None


def _json_list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _replace_previous_text(
    current: Any,
    previous: Any,
    incoming: Any,
    chapter: Chapter,
    *,
    limit: int,
    short: bool = False,
) -> str:
    current_text = str(current or "")
    previous_text = str(previous or "").strip()
    incoming_text = str(incoming or "").strip()
    if previous_text and previous_text in current_text:
        return current_text.replace(previous_text, incoming_text, 1)[:limit]
    merger = merge_short_text if short else merge_text
    return merger(current_text, incoming_text, chapter, limit=limit)


def _chapter_can_advance_character_state(
    db: Session,
    character: Character,
    chapter: Chapter,
) -> bool:
    if not character.last_updated_chapter_id or character.last_updated_chapter_id == chapter.id:
        return True
    latest = db.query(Chapter).filter(Chapter.id == character.last_updated_chapter_id).first()
    if not latest:
        return True
    return int(chapter.sort_order or 0) >= int(latest.sort_order or 0)


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
    config_payload = dict(payload)
    nested_config = payload.get("ai_config")
    if isinstance(nested_config, dict):
        for key, value in nested_config.items():
            config_payload.setdefault(key, value)
    has_config_fields = any(
        field in config_payload and config_payload.get(field) not in (None, "")
        for field in ["custom_system_prompt", "tone_style", "catchphrases", "verbosity", "emotion_tendency"]
    )
    if not has_config_fields:
        return
    config = character.ai_config or db.query(CharacterAIConfig).filter(CharacterAIConfig.character_id == character.id).first()
    if not config:
        config = CharacterAIConfig(character_id=character.id)
        db.add(config)
    character.ai_config = config
    prompt = str(config_payload.get("custom_system_prompt") or "").strip()
    if prompt:
        config.custom_system_prompt = prompt[:12000]
    if config_payload.get("tone_style"):
        config.tone_style = str(config_payload.get("tone_style"))[:100]
    if config_payload.get("verbosity"):
        config.verbosity = str(config_payload.get("verbosity"))[:50]
    if config_payload.get("emotion_tendency"):
        config.emotion_tendency = str(config_payload.get("emotion_tendency"))[:100]
    if isinstance(config_payload.get("catchphrases"), list):
        config.catchphrases = json.dumps([str(item) for item in config_payload["catchphrases"]], ensure_ascii=False)


def _update_character_profile(character: Character, payload: dict[str, Any]) -> None:
    raw_profile = payload.get("profile")
    if not isinstance(raw_profile, dict):
        raw_profile = payload.get("profile_json")
    raw_profile = dict(raw_profile) if isinstance(raw_profile, dict) else {}
    # Tolerate models that flatten stable writing-lock fields while keeping the
    # canonical persisted shape nested under profile_json.
    for field in CHARACTER_PROFILE_FIELDS:
        if field in payload and field not in raw_profile:
            raw_profile[field] = payload.get(field)
    if not raw_profile:
        return
    profile = dict(character.profile_json or {})
    changed = False
    for field in CHARACTER_PROFILE_FIELDS:
        value = raw_profile.get(field)
        if value in (None, "", [], {}):
            continue
        if field == "reveal_chapter":
            try:
                normalized: Any = max(1, int(value))
            except (TypeError, ValueError):
                continue
        else:
            normalized = str(value).strip()[:4000]
        if profile.get(field) != normalized:
            profile[field] = normalized
            changed = True
    if changed:
        character.profile_json = profile


def _identity_from_payload(payload: dict[str, Any]) -> tuple[str, list[str]]:
    canonical = str(payload.get("name") or "").strip()
    aliases = _aliases_from_payload(payload, canonical)
    return canonical, _dedupe_aliases(canonical, aliases)


def _is_placeholder_character_name(name: str | None) -> bool:
    text = str(name or "").strip()
    return not text or text in PLACEHOLDER_CHARACTER_NAMES or text.startswith("未命名")


def _aliases_from_payload(payload: dict[str, Any], canonical_name: str | None = None) -> list[str]:
    raw_aliases = payload.get("aliases")
    if raw_aliases is None:
        return []
    if not isinstance(raw_aliases, list) or any(not isinstance(item, str) for item in raw_aliases):
        raise ValueError("角色 aliases 必须是独立字符串数组，不拆分组合姓名")
    return _dedupe_aliases(canonical_name, raw_aliases)


def _dedupe_aliases(canonical_name: str | None, aliases: list[str]) -> list[str]:
    canonical = str(canonical_name or "").strip()
    seen: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        text = str(alias or "").strip()
        if not text or text == canonical or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _write_character_aliases(db: Session, character: Character, chapter: Chapter, aliases: list[str]) -> None:
    for alias in _dedupe_aliases(character.name, aliases):
        ensure_character_alias(
            db,
            character,
            alias,
            chapter,
            alias_type="alias",
            description=f"建档识别到的角色别名/称呼：{alias}",
        )


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
