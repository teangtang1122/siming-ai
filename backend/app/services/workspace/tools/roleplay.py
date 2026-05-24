"""Character roleplay and dialogue battle workspace tools."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Character, Project
from ....prompts.character_prompts import build_roleplay_system
from ....services.context_builders import (
    _build_character_ai_context,
    _build_character_context,
    _build_character_relationships,
    _build_character_timeline,
    _build_outline_context,
    _build_recent_summaries,
    _build_scene_characters_context,
    _build_world_context,
    _get_outline_node_or_404,
)
from ....prompts.style_prompts import build_style_context
from ..utils import find_character_by_name_or_id, find_outline_by_title_or_id


async def roleplay_character(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Let a single character react to a situation — the AI roleplays as that character."""
    character_id = str(args.get("character_id") or "").strip() or None
    character_name = str(args.get("character_name") or "").strip() or None
    situation = str(args.get("situation") or args.get("prompt") or "").strip()
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None

    if not situation:
        return {"tool": "roleplay_character", "status": "skipped", "detail": "缺少场景描述（situation）", "data": {}}

    character = None
    if character_id:
        character = db.query(Character).filter(
            Character.project_id == project_id, Character.id == character_id
        ).first()
    if not character and character_name:
        character = find_character_by_name_or_id(db, project_id, character_name)
    if not character:
        label = character_name or character_id or "未知"
        return {"tool": "roleplay_character", "status": "skipped", "detail": f"未找到角色：{label}", "data": {}}

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "roleplay_character", "status": "skipped", "detail": "项目不存在", "data": {}}

    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    world_ctx = _build_world_context(db, project_id, outline_node_id)
    summaries = _build_recent_summaries(db, project_id, limit=5)
    style_ctx = build_style_context(project)

    model = str(args.get("model") or "") or None
    config = character.ai_config
    model_override = model or (config.model_override if config else None)

    messages = [
        {
            "role": "system",
            "content": build_roleplay_system(
                project_title=project.title,
                character_name=character.name,
                character_context=_build_character_context(character),
                ai_context=_build_character_ai_context(character),
                relationships=_build_character_relationships(db, project_id, character.id),
                timeline=_build_character_timeline(db, character.id),
                style_ctx=style_ctx,
                world_ctx=world_ctx,
                outline_ctx=outline_ctx,
                summaries=summaries,
            ),
        },
        {"role": "user", "content": f"场景：{situation}\n请以{character.name}的身份回应。"},
    ]

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model_override,
            temperature=float(args.get("temperature") or 0.8),
            max_tokens=int(args.get("max_tokens") or 2000),
            timeout=120,
            retry=1,
        )
    except Exception as exc:
        return {"tool": "roleplay_character", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    content = str(result.get("content") or "")[:4000]
    return {
        "tool": "roleplay_character",
        "status": "ok",
        "detail": f"角色「{character.name}」已回应",
        "data": {
            "character_id": character.id,
            "character_name": character.name,
            "content": content,
        },
    }


async def dialogue_battle(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Multi-character dialogue — each character reacts in turn, building on previous lines."""
    character_names = args.get("character_names") or []
    character_ids = args.get("character_ids") or []
    scene = str(args.get("scene") or args.get("situation") or args.get("prompt") or "").strip()
    turns = max(1, min(int(args.get("turns") or 2), 4))
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None

    if not scene:
        return {"tool": "dialogue_battle", "status": "skipped", "detail": "缺少场景描述（scene）", "data": []}

    characters: list[Character] = []
    for cid in (character_ids or []):
        ch = db.query(Character).filter(Character.project_id == project_id, Character.id == str(cid)).first()
        if ch:
            characters.append(ch)
    for name in (character_names or []):
        ch = find_character_by_name_or_id(db, project_id, str(name))
        if ch and ch not in characters:
            characters.append(ch)

    if len(characters) < 2:
        return {"tool": "dialogue_battle", "status": "skipped", "detail": "至少需要 2 个角色", "data": []}

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "dialogue_battle", "status": "skipped", "detail": "项目不存在", "data": []}

    world_ctx = _build_world_context(db, project_id, outline_node_id)
    summaries = _build_recent_summaries(db, project_id, limit=5)
    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    scene_chars = _build_scene_characters_context(db, project_id, outline_node_id)
    style_ctx = build_style_context(project)

    model = str(args.get("model") or "") or None
    dialogue_history: list[dict] = []

    try:
        for turn in range(turns):
            for char in characters:
                config = char.ai_config
                model_override = model or (config.model_override if config else None)

                history_text = "\n".join(
                    f"{h['character_name']}: {h['content']}" for h in dialogue_history[-8:]
                ) if dialogue_history else "（对话刚开始）"

                messages = [
                    {
                        "role": "system",
                        "content": build_roleplay_system(
                            project_title=project.title,
                            character_name=char.name,
                            character_context=_build_character_context(char),
                            ai_context=_build_character_ai_context(char),
                            relationships=_build_character_relationships(db, project_id, char.id),
                            timeline=_build_character_timeline(db, char.id),
                            style_ctx=style_ctx,
                            world_ctx=world_ctx,
                            outline_ctx=outline_ctx,
                            summaries=summaries,
                            is_dialogue_battle=True,
                            scene_chars=scene_chars,
                            dialogue_history=history_text,
                        ),
                    },
                    {"role": "user", "content": f"场景：{scene}\n请以 {char.name} 的身份发言。"},
                ]

                result = await LLMGateway.chat_completion(
                    messages=messages,
                    model=model_override,
                    temperature=float(args.get("temperature") or 0.8),
                    max_tokens=int(args.get("max_tokens") or 2000),
                    timeout=120,
                    retry=1,
                )
                char_content = str(result.get("content") or "")[:3000]
                dialogue_history.append({
                    "character_id": char.id,
                    "character_name": char.name,
                    "content": char_content,
                })
    except Exception as exc:
        # Return partial dialogue if LLM fails mid-way
        if not dialogue_history:
            return {"tool": "dialogue_battle", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": []}

    turn_count = turns
    return {
        "tool": "dialogue_battle",
        "status": "ok",
        "detail": f"{len(characters)} 个角色 × {turn_count} 轮对戏完成",
        "data": dialogue_history,
    }
