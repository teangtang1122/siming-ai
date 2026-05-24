"""Character Writer workspace tool — generates character cards with full crafting rules."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Character, Project
from ....prompts.character_writer_prompts import build_character_writer_messages
from ....services.context_builders import _build_world_context as _build_world_ctx
from ....prompts.style_prompts import build_style_context

CHARACTER_CARD_TOOL = {
    "type": "function",
    "function": {
        "name": "create_character",
        "description": "创建一份完整的角色卡片，包含角色名、外貌、性格、背景、能力、角色类型和设计说明。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色名"},
                "appearance": {"type": "string", "description": "外貌描写（80-150字，一个标志性特征即可，不要从头发写到脚）"},
                "personality": {"type": "string", "description": "性格特征（150-300字，含内在矛盾、行为模式、在不同情境下的表现）"},
                "background": {"type": "string", "description": "背景故事（200-400字，解释角色为什么成为现在的样子）"},
                "abilities": {"type": "array", "items": {"type": "string"}, "description": "能力列表"},
                "role_type": {"type": "string", "enum": ["protagonist", "supporting", "antagonist", "mentor", "other"], "description": "角色类型"},
                "design_notes": {"type": "string", "description": "设计说明——核心矛盾、与已有角色的关系张力、预期成长弧线"},
            },
            "required": ["name", "appearance", "personality", "background", "abilities", "role_type", "design_notes"],
        },
    },
}


def _list_existing_characters(db: Session, project_id: str) -> str:
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.created_at.desc())
        .limit(30)
        .all()
    )
    if not characters:
        return "暂无角色。"
    lines = []
    for c in characters:
        lines.append(
            f"- {c.name}（{c.role_type or '未设定'}）: "
            f"性格: {(c.personality or '未设定')[:100]}; "
            f"背景: {(c.background or '未设定')[:100]}"
        )
    return "\n".join(lines)


async def character_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Generate a character card with complete crafting rules.

    Args:
        name: Character name (optional, can be generated)
        role_type: Suggested role type (optional)
        requirements: Optional writing requirements / direction

    Returns:
        {"tool": "character_writer", "status": "ok", "data": {"character": {...}}}
    """
    name_hint = str(args.get("name") or "").strip()
    role_hint = str(args.get("role_type") or "").strip()
    requirements = str(args.get("requirements") or "").strip()

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "character_writer", "status": "skipped", "detail": "项目不存在", "data": {}}

    style_ctx = build_style_context(project, include_anti_ai=False)
    world_ctx = _build_world_ctx(db, project_id)
    existing = _list_existing_characters(db, project_id)

    messages = build_character_writer_messages(
        style_context=style_ctx,
        world_context=world_ctx,
        existing_characters=existing,
        requirements=requirements,
        name_hint=name_hint,
        role_hint=role_hint,
    )

    model = str(args.get("model") or "") or None
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.8,
            max_tokens=3000,
            timeout=120,
            retry=1,
            tools=[CHARACTER_CARD_TOOL],
            tool_choice="required",
        )
    except Exception as exc:
        return {"tool": "character_writer", "status": "error", "detail": f"角色生成失败: {exc}", "data": {}}

    tool_calls = result.get("tool_calls") or []
    if not tool_calls:
        return {
            "tool": "character_writer",
            "status": "error",
            "detail": "角色生成结果解析失败",
            "data": {"raw": str(result.get("content", ""))[:500]},
        }

    try:
        parsed = _json.loads(tool_calls[0]["function"]["arguments"])
    except (_json.JSONDecodeError, AttributeError):
        return {
            "tool": "character_writer",
            "status": "error",
            "detail": "角色生成结果解析失败",
            "data": {"raw": tool_calls[0].get("function", {}).get("arguments", "")[:500]},
        }

    if not parsed.get("name"):
        return {
            "tool": "character_writer",
            "status": "error",
            "detail": "角色生成结果解析失败",
            "data": {"raw": _json.dumps(parsed, ensure_ascii=False)[:500]},
        }

    return {
        "tool": "character_writer",
        "status": "ok",
        "detail": f"已生成角色卡片：{parsed.get('name', '')}",
        "data": {"character": parsed},
    }
