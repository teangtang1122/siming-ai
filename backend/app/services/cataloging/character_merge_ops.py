"""Apply character merge candidates."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    Chapter,
    ChapterCharacter,
    Character,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterTimeline,
    OutlineNodeCharacter,
)
from .character_ops import ensure_character_version
from .alias_ops import ensure_character_alias
from .background_compactor import merge_background
from .lookups import find_character_by_name_or_id
from .merge import merge_json_list, merge_short_text, merge_text
from .snapshots import character_snapshot


def apply_character_merge_candidate(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    primary_name = str(payload.get("primary_name") or payload.get("canonical_name") or "").strip()
    secondary_name = str(payload.get("secondary_name") or "").strip()
    if not primary_name or not secondary_name:
        raise ValueError("角色合并候选缺少 primary_name 或 secondary_name")

    primary = find_character_by_name_or_id(db, chapter.project_id, primary_name)
    secondary = find_character_by_name_or_id(db, chapter.project_id, secondary_name)
    if not primary or not secondary:
        raise ValueError("角色合并需要两个已存在角色")
    if primary.id == secondary.id:
        raise ValueError("角色合并不能指向同一角色")

    old = {
        "primary": character_snapshot(primary),
        "secondary": character_snapshot(secondary),
    }
    canonical_name = str(payload.get("canonical_name") or "").strip()
    aliases = [str(item).strip() for item in payload.get("aliases") or [] if str(item).strip()]
    aliases.extend([primary.name, secondary.name])
    aliases = list(dict.fromkeys(aliases))

    _rename_primary_if_safe(db, chapter.project_id, primary, canonical_name)
    for alias in aliases:
        ensure_character_alias(
            db,
            primary,
            alias,
            chapter,
            alias_type="merged_identity" if alias == secondary.name else "alias",
            description=payload.get("confidence_reason") or payload.get("background_append"),
            confidence=candidate.confidence,
            merged_character_id=secondary.id if alias == secondary.name else None,
        )
    _merge_profile(primary, secondary, aliases, payload, chapter)
    _move_links(db, primary, secondary)
    _mark_secondary_alias(secondary, primary, payload, chapter)

    primary.last_updated_chapter_id = chapter.id
    primary.last_seen_chapter_id = chapter.id
    primary.updated_at = datetime.utcnow()
    secondary.last_updated_chapter_id = chapter.id
    secondary.updated_at = datetime.utcnow()
    ensure_character_version(db, primary, chapter, {
        "change_summary": f"合并角色身份：{secondary.name} -> {primary.name}",
        "event_description": payload.get("confidence_reason") or payload.get("background_append"),
    }, False)
    ensure_character_version(db, secondary, chapter, {
        "change_summary": f"标记为 {primary.name} 的合并身份",
        "event_description": payload.get("confidence_reason") or payload.get("background_append"),
    }, False)

    return {
        "target_type": "character",
        "target_id": primary.id,
        "old_value": old,
        "new_value": {
            "primary": character_snapshot(primary),
            "secondary": character_snapshot(secondary),
        },
        "detail": f"角色合并候选已应用: {secondary.name} -> {primary.name}",
    }


def _rename_primary_if_safe(db: Session, project_id: str, primary: Character, canonical_name: str) -> None:
    if not canonical_name or canonical_name == primary.name:
        return
    existing = find_character_by_name_or_id(db, project_id, canonical_name)
    if existing and existing.id != primary.id:
        return
    primary.name = canonical_name[:100]


def _merge_profile(
    primary: Character,
    secondary: Character,
    aliases: list[str],
    payload: dict[str, Any],
    chapter: Chapter,
) -> None:
    identity_note = _identity_note(primary, secondary, aliases, payload)
    primary.background = merge_background(primary.background, secondary.background, chapter, limit=4000)
    primary.background = merge_background(primary.background, payload.get("background_append") or identity_note, chapter, limit=4000)
    primary.appearance = merge_text(primary.appearance, secondary.appearance, chapter, limit=8000)
    primary.personality = merge_text(primary.personality, secondary.personality, chapter, limit=8000)
    primary.abilities = merge_json_list(primary.abilities, _parse_list(secondary.abilities))
    primary.items_or_assets = merge_short_text(primary.items_or_assets, secondary.items_or_assets, chapter, limit=4000)
    primary.abilities_state = merge_short_text(primary.abilities_state, secondary.abilities_state, chapter, limit=4000)
    primary.active_conflict = merge_short_text(primary.active_conflict, secondary.active_conflict, chapter, limit=4000)
    if secondary.ai_config and primary.ai_config:
        primary.ai_config.custom_system_prompt = merge_text(
            primary.ai_config.custom_system_prompt,
            secondary.ai_config.custom_system_prompt,
            chapter,
            limit=12000,
        )
    if primary.ai_config:
        primary.ai_config.custom_system_prompt = merge_text(
            primary.ai_config.custom_system_prompt,
            f"身份合并：该角色可能使用过这些称呼或身份：{', '.join(aliases)}。扮演时必须保持这些经历的一致性。",
            chapter,
            limit=12000,
        )


def _move_links(db: Session, primary: Character, secondary: Character) -> None:
    for event in db.query(CharacterTimeline).filter(CharacterTimeline.character_id == secondary.id).all():
        event.character_id = primary.id
    for change in db.query(CharacterChangeLog).filter(CharacterChangeLog.character_id == secondary.id).all():
        change.character_id = primary.id
    for link in db.query(OutlineNodeCharacter).filter(OutlineNodeCharacter.character_id == secondary.id).all():
        link.character_id = primary.id
    for appearance in db.query(ChapterCharacter).filter(ChapterCharacter.character_id == secondary.id).all():
        duplicate = (
            db.query(ChapterCharacter)
            .filter(ChapterCharacter.chapter_id == appearance.chapter_id, ChapterCharacter.character_id == primary.id)
            .first()
        )
        if duplicate:
            duplicate.description = merge_short_text(duplicate.description, appearance.description, appearance.chapter, limit=4000)
            db.delete(appearance)
        else:
            appearance.character_id = primary.id
    for relationship in db.query(CharacterRelationship).filter(
        (CharacterRelationship.character_a_id == secondary.id)
        | (CharacterRelationship.character_b_id == secondary.id)
    ).all():
        if relationship.character_a_id == secondary.id:
            relationship.character_a_id = primary.id
        if relationship.character_b_id == secondary.id:
            relationship.character_b_id = primary.id
        if relationship.character_a_id == relationship.character_b_id:
            db.delete(relationship)


def _mark_secondary_alias(
    secondary: Character,
    primary: Character,
    payload: dict[str, Any],
    chapter: Chapter,
) -> None:
    note = (
        f"该角色卡已作为身份/马甲合并到“{primary.name}”。"
        f"合并依据：{payload.get('confidence_reason') or payload.get('evidence') or '用户确认的角色合并候选'}"
    )
    secondary.role_type = "merged_alias"
    secondary.background = merge_background(secondary.background, note, chapter, limit=3000)
    secondary.current_goal = ""
    secondary.active_conflict = ""


def _identity_note(primary: Character, secondary: Character, aliases: list[str], payload: dict[str, Any]) -> str:
    evidence_points = payload.get("evidence_points") or []
    if isinstance(evidence_points, list):
        evidence = "；".join(str(item) for item in evidence_points if str(item).strip())
    else:
        evidence = str(evidence_points)
    return (
        f"身份合并记录：{secondary.name} 被确认或高度疑似为 {primary.name} 的另一身份。"
        f"已知称呼：{', '.join(aliases)}。"
        f"依据：{payload.get('confidence_reason') or evidence or '暂无详细依据'}。"
    )


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
