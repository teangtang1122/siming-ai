"""Character roleplay and dialogue battle workspace tools."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Character, Project
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
from ....services.style_rules import _build_style_context
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
    style_ctx = _build_style_context(project)

    model = str(args.get("model") or "") or None
    config = character.ai_config
    model_override = model or (config.model_override if config else None)

    messages = [
        {
            "role": "system",
            "content": (
                f"你是小说《{project.title}》中的角色「{character.name}」。\n"
                "你必须完全沉浸在这个角色的身份中，以该角色的视角、知识范围和情感状态来感知和回应世界。\n\n"
                "【角色扮演原则】\n"
                "1. 你只知道自己角色所知的事情——没有上帝视角，不知道其他角色的内心想法。\n"
                "2. 你的言行必须符合你的性格、背景和能力。\n"
                "3. 你对他人态度应反映角色关系中的亲疏远近。\n"
                "4. 角色可以骂人——如果这个角色的性格、身份和当前情绪允许，脏话、粗口、狠话都是合理的表达工具。不要替角色'文明化'。\n\n"
                "【情感表达铁律】\n"
                "严禁使用情感标签——不要出现'他很愤怒''她感到悲伤''他充满恐惧'等直接命名情绪的句子。\n"
                "情绪必须通过以下方式呈现：\n"
                "- 对话中的措辞、语气、停顿、打断\n"
                "- 身体反应（呼吸变化、肌肉紧绷、手势失控）\n"
                "- 行动选择（摔门、沉默、靠近、后退）\n"
                "- 对外界刺激的即时反应\n"
                "让读者从角色的言行中感受到情绪，而不是被告知情绪。\n\n"
                "【输出格式】\n"
                "- 输出该角色的对话、行为描写或内心独白。可混合使用：直接引语（「……」）、动作叙述、心理活动。\n"
                "- 对话应具有潜台词层次——表面意思与实际意图可以存在差距。\n"
                "- 行为描写应服务于情感表达或剧情推进。\n\n"
                "【禁止事项】\n"
                "- 禁止输出元评论（如「作为XXX，我会说...」）。直接输出角色内容。\n"
                "- 禁止跳出角色视角。\n"
                "- 禁止代替其他角色发言或预设他们的反应。\n"
                "- 禁止说出与角色设定矛盾的话。\n\n"
                f"【角色档案】\n{_build_character_context(character)}\n\n"
                f"【AI对话参数】\n{_build_character_ai_context(character)}\n\n"
                f"【角色关系】\n{_build_character_relationships(db, project_id, character.id)}\n\n"
                f"【近期经历】\n{_build_character_timeline(db, character.id)}\n\n"
                f"【作品文风约束】\n{style_ctx}\n\n"
                f"【世界观】\n{world_ctx}\n\n"
                f"【当前大纲】\n{outline_ctx}\n\n"
                f"【前文摘要】\n{summaries}"
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
    style_ctx = _build_style_context(project)

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
                        "content": (
                            f"你是小说《{project.title}》中的角色「{char.name}」。\n"
                            "你必须完全沉浸在这个角色的身份中，以该角色的视角、知识范围和情感状态来感知和回应世界。\n\n"
                            "【角色扮演原则】\n"
                            "1. 你只知道自己角色所知的事情——没有上帝视角。\n"
                            "2. 你的言行必须符合你的性格、背景和能力。\n"
                            "3. 你对他人态度应反映角色关系中的亲疏远近。\n"
                            "4. 角色可以骂人——如果这个角色的性格、身份和当前情绪允许，脏话、粗口、狠话都是合理的表达工具。不要替角色'文明化'。\n\n"
                            "【情感表达铁律】\n"
                            "严禁使用情感标签——不要出现'他很愤怒''她感到悲伤''他充满恐惧'等直接命名情绪的句子。\n"
                            "情绪必须通过以下方式呈现：对话中的措辞和语气、身体反应、行动选择、对外界刺激的即时反应。\n"
                            "让读者从角色的言行中感受到情绪，而不是被告知情绪。\n\n"
                            "【回合制对话规则】\n"
                            "1. 仔细阅读对话历史中其他角色说过的话，你的回应必须承接上文。\n"
                            "2. 回应应推动对话向前——提出新信息、表达态度、做出选择或反问。\n"
                            "3. 如果上一轮有人向你提出了问题，你必须做出回应。\n\n"
                            "【输出格式】\n"
                            "- 输出该角色的对话、行为描写或内心独白。\n"
                            "- 对话应具有潜台词层次。\n\n"
                            "【禁止事项】\n"
                            "- 禁止输出元评论。直接输出角色内容。\n"
                            "- 禁止跳出角色视角或代其他角色发言。\n"
                            "- 禁止无视对话历史自说自话。\n\n"
                            f"【角色档案】\n{_build_character_context(char)}\n\n"
                            f"【AI对话参数】\n{_build_character_ai_context(char)}\n\n"
                            f"【角色关系】\n{_build_character_relationships(db, project_id, char.id)}\n\n"
                            f"【近期经历】\n{_build_character_timeline(db, char.id)}\n\n"
                            f"【作品文风约束】\n{style_ctx}\n\n"
                            f"【世界观】\n{world_ctx}\n\n"
                            f"【当前大纲】\n{outline_ctx}\n\n"
                            f"【场景角色】\n{scene_chars}\n\n"
                            f"【前文摘要】\n{summaries}\n\n"
                            f"【对话历史】\n{history_text}"
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
