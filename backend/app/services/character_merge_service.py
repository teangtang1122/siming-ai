"""Shared character merge and duplicate-detection service."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database.models import (
    Chapter,
    ChapterCharacter,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterTimeline,
    CharacterVersion,
    OutlineNodeCharacter,
)
from .cataloging.alias_ops import ensure_character_alias
from .cataloging.background_compactor import merge_background
from .cataloging.merge import merge_json_list, merge_short_text, merge_text
from .cataloging.name_utils import derived_character_aliases, normalize_name_key, split_character_name
from .cataloging.snapshots import character_snapshot


def find_duplicate_character_candidates(db: Session, project_id: str, limit: int = 80) -> list[dict[str, Any]]:
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter(or_(Character.role_type.is_(None), Character.role_type != "merged_alias"))
        .order_by(Character.updated_at.desc())
        .limit(500)
        .all()
    )
    results: list[dict[str, Any]] = []
    for index, left in enumerate(characters):
        for right in characters[index + 1:]:
            score, reasons = _duplicate_score(left, right)
            if score < 0.55:
                continue
            primary, secondary = _default_primary(left, right)
            aliases = _merged_aliases(primary, secondary, [])
            results.append({
                "primary": _character_brief(primary),
                "secondary": _character_brief(secondary),
                "canonical_name": primary.name,
                "aliases": aliases,
                "score": round(min(score, 0.99), 2),
                "reasons": reasons,
            })
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def build_character_merge_preview(
    db: Session,
    project_id: str,
    primary_id: str,
    secondary_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary, secondary = _load_merge_pair(db, project_id, primary_id, secondary_id)
    options = options or {}
    aliases = _merged_aliases(primary, secondary, options.get("aliases") or [])
    canonical_name = str(options.get("canonical_name") or primary.name).strip() or primary.name
    reason = str(options.get("confidence_reason") or options.get("reason") or "用户确认的重复角色合并").strip()
    return {
        "primary": character_snapshot(primary),
        "secondary": character_snapshot(secondary),
        "canonical_name": canonical_name,
        "aliases": aliases,
        "reason": reason,
        "stats": {
            "secondary_chapter_appearances": db.query(ChapterCharacter).filter(ChapterCharacter.character_id == secondary.id).count(),
            "secondary_outline_links": db.query(OutlineNodeCharacter).filter(OutlineNodeCharacter.character_id == secondary.id).count(),
            "secondary_timeline_events": db.query(CharacterTimeline).filter(CharacterTimeline.character_id == secondary.id).count(),
            "secondary_relationships": db.query(CharacterRelationship).filter(
                or_(CharacterRelationship.character_a_id == secondary.id, CharacterRelationship.character_b_id == secondary.id)
            ).count(),
            "secondary_aliases": db.query(CharacterAlias).filter(CharacterAlias.character_id == secondary.id).count(),
        },
        "merged_preview": {
            "name": canonical_name,
            "aliases": aliases,
            "background": merge_background(
                merge_background(primary.background, secondary.background, None, limit=4000),
                options.get("background_append") or _identity_note(primary, secondary, aliases, reason, options.get("evidence_points")),
                None,
                limit=4000,
            ),
            "appearance": merge_text(primary.appearance, secondary.appearance, None, limit=1200),
            "personality": merge_text(primary.personality, secondary.personality, None, limit=1200),
            "abilities": _merge_ability_preview(primary, secondary),
        },
    }


def merge_characters(
    db: Session,
    project_id: str,
    primary_id: str,
    secondary_id: str,
    options: dict[str, Any] | None = None,
    source_chapter: Chapter | None = None,
) -> dict[str, Any]:
    primary, secondary = _load_merge_pair(db, project_id, primary_id, secondary_id)
    options = options or {}
    old = {"primary": character_snapshot(primary), "secondary": character_snapshot(secondary)}
    canonical_name = str(options.get("canonical_name") or primary.name).strip()
    aliases = _merged_aliases(primary, secondary, options.get("aliases") or [])
    reason = str(options.get("confidence_reason") or options.get("reason") or "用户确认的重复角色合并").strip()

    _rename_primary_if_safe(db, project_id, primary, canonical_name)
    for alias in aliases:
        ensure_character_alias(
            db,
            primary,
            alias,
            source_chapter,
            alias_type="merged_identity" if alias == secondary.name else "alias",
            description=reason,
            confidence=_float_or_none(options.get("confidence")),
            merged_character_id=secondary.id if alias == secondary.name else None,
        )

    _merge_profile(primary, secondary, aliases, options, reason, source_chapter)
    _move_links(db, primary, secondary)
    _move_aliases(db, primary, secondary, source_chapter, reason)
    _mark_secondary_alias(secondary, primary, reason, source_chapter)

    primary.last_updated_chapter_id = source_chapter.id if source_chapter else primary.last_updated_chapter_id
    primary.last_seen_chapter_id = source_chapter.id if source_chapter else primary.last_seen_chapter_id
    primary.updated_at = datetime.utcnow()
    secondary.last_updated_chapter_id = source_chapter.id if source_chapter else secondary.last_updated_chapter_id
    secondary.updated_at = datetime.utcnow()
    _create_merge_version(db, primary, f"合并重复角色：{secondary.name} -> {primary.name}", source_chapter)
    _create_merge_version(db, secondary, f"标记为 {primary.name} 的合并身份", source_chapter)

    return {
        "target_type": "character",
        "target_id": primary.id,
        "old_value": old,
        "new_value": {
            "primary": character_snapshot(primary),
            "secondary": character_snapshot(secondary),
        },
        "detail": f"角色已合并: {secondary.name} -> {primary.name}",
    }


def _load_merge_pair(db: Session, project_id: str, primary_id: str, secondary_id: str) -> tuple[Character, Character]:
    primary = db.query(Character).filter(Character.project_id == project_id, Character.id == primary_id).first()
    secondary = db.query(Character).filter(Character.project_id == project_id, Character.id == secondary_id).first()
    if not primary or not secondary:
        raise ValueError("合并角色必须属于当前作品")
    if primary.id == secondary.id:
        raise ValueError("不能合并同一张角色卡")
    return primary, secondary


def _duplicate_score(left: Character, right: Character) -> tuple[float, list[str]]:
    left_names = _identity_names(left)
    right_names = _identity_names(right)
    left_keys = {normalize_name_key(name) for name in left_names if normalize_name_key(name)}
    right_keys = {normalize_name_key(name) for name in right_names if normalize_name_key(name)}
    reasons: list[str] = []
    score = 0.0

    if left_keys & right_keys:
        score += 0.75
        reasons.append("名称或别名完全命中")
    elif any(a and b and (a in b or b in a) and min(len(a), len(b)) >= 2 for a in left_keys for b in right_keys):
        score += 0.55
        reasons.append("名称存在包含关系")

    derived_left = {normalize_name_key(alias) for name in left_names for alias in derived_character_aliases(name)}
    derived_right = {normalize_name_key(alias) for name in right_names for alias in derived_character_aliases(name)}
    if (derived_left & right_keys) or (derived_right & left_keys):
        score += 0.3
        reasons.append("亲属称呼/尊称可互相推导")

    if left.role_type and right.role_type and left.role_type == right.role_type:
        score += 0.05
    if _shared_background_terms(left, right) >= 2:
        score += 0.15
        reasons.append("背景关键词重合")
    return score, reasons


def _identity_names(character: Character) -> list[str]:
    names = [character.name, *split_character_name(character.name)]
    names.extend(alias.alias for alias in character.aliases or [])
    names.extend(derived_character_aliases(character.name))
    return list(dict.fromkeys(str(item).strip() for item in names if str(item).strip()))


def _default_primary(left: Character, right: Character) -> tuple[Character, Character]:
    if left.role_type == "merged_alias":
        return right, left
    if right.role_type == "merged_alias":
        return left, right
    if len(split_character_name(left.name)) > len(split_character_name(right.name)):
        return right, left
    if len(split_character_name(right.name)) > len(split_character_name(left.name)):
        return left, right
    return (left, right) if (left.current_version or 1) >= (right.current_version or 1) else (right, left)


def _merged_aliases(primary: Character, secondary: Character, aliases: list[Any]) -> list[str]:
    merged = []
    for value in [*aliases, *_identity_names(primary), *_identity_names(secondary)]:
        for name in split_character_name(str(value)):
            if name and name != primary.name:
                merged.append(name)
        text = str(value or "").strip()
        if text and text != primary.name:
            merged.append(text)
    return list(dict.fromkeys(merged))


def _rename_primary_if_safe(db: Session, project_id: str, primary: Character, canonical_name: str) -> None:
    if not canonical_name or canonical_name == primary.name:
        return
    existing = db.query(Character).filter(
        Character.project_id == project_id,
        Character.name == canonical_name,
        Character.id != primary.id,
    ).first()
    if not existing:
        primary.name = canonical_name[:100]


def _merge_profile(
    primary: Character,
    secondary: Character,
    aliases: list[str],
    options: dict[str, Any],
    reason: str,
    chapter: Chapter | None,
) -> None:
    identity_note = options.get("background_append") or _identity_note(primary, secondary, aliases, reason, options.get("evidence_points"))
    primary.background = merge_background(primary.background, secondary.background, chapter, limit=4000)
    primary.background = merge_background(primary.background, identity_note, chapter, limit=4000)
    primary.appearance = merge_text(primary.appearance, secondary.appearance, chapter, limit=8000)
    primary.personality = merge_text(primary.personality, secondary.personality, chapter, limit=8000)
    primary.abilities = merge_json_list(primary.abilities, _parse_list(secondary.abilities))
    for field in ["items_or_assets", "abilities_state", "active_conflict"]:
        setattr(primary, field, merge_short_text(getattr(primary, field), getattr(secondary, field), chapter, limit=4000))
    for field in ["life_status", "current_location", "realm_or_level", "physical_state", "mental_state", "current_goal"]:
        if not getattr(primary, field) and getattr(secondary, field):
            setattr(primary, field, getattr(secondary, field))
    _merge_ai_config(primary, secondary, aliases, chapter)


def _merge_ai_config(primary: Character, secondary: Character, aliases: list[str], chapter: Chapter | None) -> None:
    if not secondary.ai_config and not primary.ai_config:
        return
    if not primary.ai_config:
        primary.ai_config = CharacterAIConfig(character_id=primary.id)
    if secondary.ai_config:
        primary.ai_config.custom_system_prompt = merge_text(
            primary.ai_config.custom_system_prompt,
            secondary.ai_config.custom_system_prompt,
            chapter,
            limit=12000,
        )
        for field in ["tone_style", "verbosity", "emotion_tendency", "model_override"]:
            if not getattr(primary.ai_config, field) and getattr(secondary.ai_config, field):
                setattr(primary.ai_config, field, getattr(secondary.ai_config, field))
        primary.ai_config.catchphrases = merge_json_list(primary.ai_config.catchphrases, _parse_list(secondary.ai_config.catchphrases))
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
        duplicate = db.query(OutlineNodeCharacter).filter(
            OutlineNodeCharacter.outline_node_id == link.outline_node_id,
            OutlineNodeCharacter.character_id == primary.id,
        ).first()
        if duplicate:
            duplicate.role_in_scene = merge_short_text(duplicate.role_in_scene, link.role_in_scene, None, limit=1000)
            db.delete(link)
        else:
            link.character_id = primary.id
    for appearance in db.query(ChapterCharacter).filter(ChapterCharacter.character_id == secondary.id).all():
        duplicate = db.query(ChapterCharacter).filter(
            ChapterCharacter.chapter_id == appearance.chapter_id,
            ChapterCharacter.character_id == primary.id,
        ).first()
        if duplicate:
            duplicate.description = merge_short_text(duplicate.description, appearance.description, appearance.chapter, limit=4000)
            db.delete(appearance)
        else:
            appearance.character_id = primary.id
    for relationship in db.query(CharacterRelationship).filter(
        or_(CharacterRelationship.character_a_id == secondary.id, CharacterRelationship.character_b_id == secondary.id)
    ).all():
        if relationship.character_a_id == secondary.id:
            relationship.character_a_id = primary.id
        if relationship.character_b_id == secondary.id:
            relationship.character_b_id = primary.id
        if relationship.character_a_id == relationship.character_b_id:
            db.delete(relationship)
            continue
        duplicate = db.query(CharacterRelationship).filter(
            CharacterRelationship.id != relationship.id,
            CharacterRelationship.project_id == relationship.project_id,
            CharacterRelationship.character_a_id == relationship.character_a_id,
            CharacterRelationship.character_b_id == relationship.character_b_id,
            CharacterRelationship.relationship_type == relationship.relationship_type,
        ).first()
        if duplicate:
            duplicate.description = merge_short_text(duplicate.description, relationship.description, None, limit=4000)
            db.delete(relationship)


def _move_aliases(db: Session, primary: Character, secondary: Character, chapter: Chapter | None, reason: str) -> None:
    for alias in db.query(CharacterAlias).filter(CharacterAlias.character_id == secondary.id).all():
        ensure_character_alias(
            db,
            primary,
            alias.alias,
            chapter,
            alias_type=alias.alias_type or "alias",
            description=alias.description or reason,
            confidence=alias.confidence,
            merged_character_id=alias.merged_character_id,
        )
        db.delete(alias)


def _mark_secondary_alias(secondary: Character, primary: Character, reason: str, chapter: Chapter | None) -> None:
    note = f"该角色卡已作为重复身份合并到“{primary.name}”。合并依据：{reason}"
    secondary.role_type = "merged_alias"
    secondary.background = merge_background(secondary.background, note, chapter, limit=3000)
    secondary.current_goal = ""
    secondary.active_conflict = ""


def _create_merge_version(db: Session, character: Character, summary: str, chapter: Chapter | None) -> None:
    character.current_version = (character.current_version or 1) + 1
    db.flush()
    db.add(CharacterVersion(
        character_id=character.id,
        version_number=character.current_version,
        snapshot_data=json.dumps(character_snapshot(character), ensure_ascii=False),
        change_summary=summary,
        source_chapter_id=chapter.id if chapter else None,
    ))


def _identity_note(
    primary: Character,
    secondary: Character,
    aliases: list[str],
    reason: str,
    evidence_points: Any = None,
) -> str:
    if isinstance(evidence_points, list):
        evidence = "；".join(str(item) for item in evidence_points if str(item).strip())
    else:
        evidence = str(evidence_points or "")
    return (
        f"身份合并记录：{secondary.name} 被确认或高度疑似为 {primary.name} 的另一身份。"
        f"已知称呼：{', '.join(aliases)}。依据：{reason or evidence or '用户确认'}。"
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


def _merge_ability_preview(primary: Character, secondary: Character) -> list[str]:
    return list(dict.fromkeys([*_parse_list(primary.abilities), *_parse_list(secondary.abilities)]))


def _shared_background_terms(left: Character, right: Character) -> int:
    left_text = normalize_name_key("".join([left.background or "", left.personality or "", left.current_location or ""]))
    right_text = normalize_name_key("".join([right.background or "", right.personality or "", right.current_location or ""]))
    terms = {term for term in re_split_terms(left_text) if len(term) >= 2}
    return sum(1 for term in terms if term in right_text)


def re_split_terms(text: str) -> list[str]:
    return [text[index:index + 2] for index in range(max(0, len(text) - 1))]


def _character_brief(character: Character) -> dict[str, Any]:
    return {
        "id": character.id,
        "name": character.name,
        "aliases": [alias.alias for alias in character.aliases or [] if alias.alias],
        "role_type": character.role_type,
        "current_version": character.current_version,
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
