"""Worldbuilding Writer workspace tool — generates worldbuilding entries with dimension-specific rules."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Project
from ....prompts.worldbuilding_writer_prompts import build_worldbuilding_writer_messages
from ....prompts.style_prompts import build_style_context

WORLDBUILDING_ENTRY_TOOL = {
    "type": "function",
    "function": {
        "name": "create_worldbuilding_entry",
        "description": "创建一条世界观设定条目，包含标题、正文、维度、剧情用途和设计说明。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "条目标题"},
                "content": {"type": "string", "description": "条目正文（200-500字，具体有料，不空洞）"},
                "dimension": {"type": "string", "enum": ["geography", "history", "factions", "power_system", "races", "culture"], "description": "设定维度"},
                "plot_usage": {"type": "string", "description": "这个设定在剧情中可以用来做什么？能制造什么冲突或困境？"},
                "design_notes": {"type": "string", "description": "与哪些已有设定有关联？为什么现在创建这个设定？"},
            },
            "required": ["title", "content", "dimension", "plot_usage", "design_notes"],
        },
    },
}


async def worldbuilding_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Generate a worldbuilding entry with dimension-specific guidance.

    Args:
        dimension: Target dimension (geography|history|factions|power_system|races|culture)
        title_hint: Suggested entry title (optional)
        requirements: Optional writing requirements

    Returns:
        {"tool": "worldbuilding_writer", "status": "ok", "data": {"entry": {...}}}
    """
    dimension = str(args.get("dimension") or "").strip() or "culture"
    title_hint = str(args.get("title") or "").strip()
    requirements = str(args.get("requirements") or "").strip()

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "worldbuilding_writer", "status": "skipped", "detail": "项目不存在", "data": {}}

    style_ctx = build_style_context(project, include_anti_ai=False)
    from ....services.context_builders import _build_world_context as _wc
    world_ctx = _wc(db, project_id)

    messages = build_worldbuilding_writer_messages(
        style_context=style_ctx,
        world_context=world_ctx,
        requirements=requirements,
        dimension=dimension,
        title_hint=title_hint,
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
            tools=[WORLDBUILDING_ENTRY_TOOL],
            tool_choice="required",
        )
    except Exception as exc:
        return {"tool": "worldbuilding_writer", "status": "error", "detail": f"世界观条目生成失败: {exc}", "data": {}}

    tool_calls = result.get("tool_calls") or []
    if not tool_calls:
        return {
            "tool": "worldbuilding_writer",
            "status": "error",
            "detail": "世界观条目生成结果解析失败",
            "data": {"raw": str(result.get("content", ""))[:500]},
        }

    try:
        parsed = _json.loads(tool_calls[0]["function"]["arguments"])
    except (_json.JSONDecodeError, AttributeError):
        return {
            "tool": "worldbuilding_writer",
            "status": "error",
            "detail": "世界观条目生成结果解析失败",
            "data": {"raw": tool_calls[0].get("function", {}).get("arguments", "")[:500]},
        }

    if not parsed.get("title"):
        return {
            "tool": "worldbuilding_writer",
            "status": "error",
            "detail": "世界观条目生成结果解析失败",
            "data": {"raw": _json.dumps(parsed, ensure_ascii=False)[:500]},
        }

    return {
        "tool": "worldbuilding_writer",
        "status": "ok",
        "detail": f"已生成世界观条目：{parsed.get('title', '')}",
        "data": {"entry": parsed},
    }
