"""Character workspace tools."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Character, CharacterAIConfig, CharacterVersion
from ..utils import character_payload, find_character_by_name_or_id


async def create_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        return {"tool": "create_character", "status": "skipped", "detail": "角色名为空"}

    from ..run_recovery import generate_idempotency_key, check_idempotency
    _idem_key = generate_idempotency_key(db, "create_character", project_id, args)
    if _idem_key:
        _existing = check_idempotency(db, project_id, _idem_key)
        if _existing:
            return _existing
    background_parts = [str(args.get("background") or "").strip()]
    for label, key in [("说话风格", "speech_style"), ("当前动机", "motivation"), ("核心冲突", "conflict")]:
        value = str(args.get(key) or "").strip()
        if value:
            background_parts.append(f"{label}：{value}")
    background = "\n".join(part for part in background_parts if part)
    character = Character(
        project_id=project_id,
        name=name[:100],
        appearance=str(args.get("appearance") or "")[:4000],
        personality=str(args.get("personality") or "")[:4000],
        background=background[:8000],
        abilities=json.dumps(
            args.get("abilities") if isinstance(args.get("abilities"), list) else [],
            ensure_ascii=False,
        ),
        role_type=str(args.get("role_type") or "supporting"),
        age=str(args.get("age") or "")[:100] or None,
        is_evolution_tracked=True,
        # Current-state fields
        life_status=str(args.get("life_status") or "")[:50] or None,
        current_location=str(args.get("current_location") or "")[:200] or None,
        realm_or_level=str(args.get("realm_or_level") or "")[:200] or None,
        physical_state=str(args.get("physical_state") or "")[:4000] or None,
        mental_state=str(args.get("mental_state") or "")[:4000] or None,
        current_goal=str(args.get("current_goal") or "")[:4000] or None,
        active_conflict=str(args.get("active_conflict") or "")[:4000] or None,
        abilities_state=str(args.get("abilities_state") or "")[:4000] or None,
        items_or_assets=str(args.get("items_or_assets") or "")[:4000] or None,
    )
    db.add(character)
    db.flush()
    ai_config_data = args.get("ai_config") if isinstance(args.get("ai_config"), dict) else {}
    prompt = str(
        ai_config_data.get("custom_system_prompt")
        or args.get("custom_system_prompt")
        or ""
    ).strip()
    if prompt or ai_config_data:
        db.add(CharacterAIConfig(
            character_id=character.id,
            tone_style=str(ai_config_data.get("tone_style") or args.get("tone_style") or "neutral")[:100],
            catchphrases=json.dumps(ai_config_data.get("catchphrases") or [], ensure_ascii=False),
            verbosity=str(ai_config_data.get("verbosity") or "moderate")[:50],
            emotion_tendency=str(ai_config_data.get("emotion_tendency") or "neutral")[:100],
            custom_system_prompt=prompt or None,
        ))
    return {
        "tool": "create_character",
        "status": "ok",
        "detail": f"已创建角色：{character.name}",
        "data": character_payload(character),
    }


async def update_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    character = find_character_by_name_or_id(db, project_id, args.get("id") or args.get("name"))
    if not character:
        return {"tool": "update_character", "status": "skipped", "detail": "未找到角色"}
    changed = False
    for field, limit in [("appearance", 4000), ("personality", 4000), ("background", 8000), ("role_type", 100), ("age", 100)]:
        if field in args:
            setattr(character, field, str(args.get(field) or "")[:limit])
            changed = True
    # Current-state fields
    for field, limit in [
        ("life_status", 50), ("current_location", 200), ("realm_or_level", 200),
        ("physical_state", 4000), ("mental_state", 4000), ("current_goal", 4000),
        ("active_conflict", 4000), ("abilities_state", 4000), ("items_or_assets", 4000),
    ]:
        if field in args:
            setattr(character, field, str(args.get(field) or "")[:limit] or None)
            changed = True
    extra_background = []
    for label, key in [("说话风格", "speech_style"), ("当前动机", "motivation"), ("核心冲突", "conflict")]:
        if key in args and str(args.get(key) or "").strip():
            extra_background.append(f"{label}：{str(args.get(key)).strip()}")
    if extra_background:
        current_background = str(character.background or "").strip()
        addition = "\n".join(extra_background)
        character.background = f"{current_background}\n\n{addition}".strip()[:8000]
        changed = True
    if "abilities" in args and isinstance(args.get("abilities"), list):
        character.abilities = json.dumps(args.get("abilities"), ensure_ascii=False)
        changed = True
    ai_config_data = args.get("ai_config") if isinstance(args.get("ai_config"), dict) else {}
    if ai_config_data or "custom_system_prompt" in args:
        config = character.ai_config or db.query(CharacterAIConfig).filter(CharacterAIConfig.character_id == character.id).first()
        if not config:
            config = CharacterAIConfig(character_id=character.id)
            db.add(config)
        if ai_config_data.get("tone_style"):
            config.tone_style = str(ai_config_data.get("tone_style"))[:100]
        if ai_config_data.get("catchphrases") is not None:
            config.catchphrases = json.dumps(ai_config_data.get("catchphrases") or [], ensure_ascii=False)
        if ai_config_data.get("verbosity"):
            config.verbosity = str(ai_config_data.get("verbosity"))[:50]
        if ai_config_data.get("emotion_tendency"):
            config.emotion_tendency = str(ai_config_data.get("emotion_tendency"))[:100]
        prompt = str(ai_config_data.get("custom_system_prompt") or args.get("custom_system_prompt") or "").strip()
        if prompt:
            config.custom_system_prompt = prompt
        changed = True
    if changed:
        character.current_version = (character.current_version or 1) + 1
        character.updated_at = datetime.utcnow()
        db.add(CharacterVersion(
            character_id=character.id,
            version_number=character.current_version,
            snapshot_data=json.dumps(character_payload(character), ensure_ascii=False),
            change_summary="AI助手调整角色档案",
        ))
    return {
        "tool": "update_character",
        "status": "ok",
        "detail": f"已更新角色：{character.name}",
        "data": character_payload(character),
    }


async def delete_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    character = find_character_by_name_or_id(db, project_id, args.get("id") or args.get("name"))
    if not character:
        return {"tool": "delete_character", "status": "skipped", "detail": "未找到角色"}
    name = character.name
    db.delete(character)
    db.flush()
    return {"tool": "delete_character", "status": "ok", "detail": f"已删除角色：{name}"}
