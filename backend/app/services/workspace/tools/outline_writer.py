"""Outline Writer workspace tool — generates outline nodes with structure rules."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Character, OutlineNode, Project
from ....prompts.outline_writer_prompts import build_outline_writer_messages
from ....prompts.style_prompts import build_style_context

OUTLINE_NODES_TOOL = {
    "type": "function",
    "function": {
        "name": "create_outline_nodes",
        "description": "创建一个或多个大纲节点。每个节点必须有标题、类型、摘要和涉及的角色名。",
        "parameters": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "节点标题"},
                            "node_type": {"type": "string", "enum": ["chapter", "volume", "section"], "description": "节点类型"},
                            "summary": {"type": "string", "description": "剧情摘要（100-300字）"},
                            "character_names": {"type": "array", "items": {"type": "string"}, "description": "涉及的角色名列表"},
                            "status": {"type": "string", "enum": ["pending"], "description": "节点状态"},
                        },
                        "required": ["title", "node_type", "summary", "character_names", "status"],
                    },
                    "description": "大纲节点列表（1-8个）",
                },
                "design_notes": {"type": "string", "description": "为什么这样设计？这些节点如何推进主线？节奏变化在哪？"},
            },
            "required": ["nodes", "design_notes"],
        },
    },
}


async def outline_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Generate outline nodes with story structure knowledge.

    Args:
        parent_id: Optional parent outline node ID
        requirements: Optional writing requirements
        batch_count: Number of nodes to generate (default 1, max 8)

    Returns:
        {"tool": "outline_writer", "status": "ok", "data": {"nodes": [...]}}
    """
    parent_id = str(args.get("parent_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()
    batch_count = max(1, min(8, int(args.get("batch_count") or 1)))

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "outline_writer", "status": "skipped", "detail": "项目不存在", "data": {}}

    # Gather existing outline tree
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )
    if nodes:
        id_to_node = {n.id: n for n in nodes}
        lines = []
        for n in nodes:
            depth = 0
            cur = n.parent_id
            while cur and cur in id_to_node:
                depth += 1
                cur = id_to_node[cur].parent_id
            indent = "  " * depth
            char_str = f" [角色: {', '.join(lc.character.name for lc in n.linked_characters if lc.character)}]" if n.linked_characters else ""
            lines.append(f"{indent}- [{n.node_type}] {n.title} ({n.status or 'pending'}){char_str}")
        existing_outline = "\n".join(lines) if lines else "暂无大纲。"
    else:
        existing_outline = "暂无大纲。"
        parent_context = ""
        if parent_id:
            return {"tool": "outline_writer", "status": "skipped", "detail": "指定了父节点但项目无大纲", "data": {}}

    # Parent context
    parent_context = ""
    if parent_id and nodes:
        parent = db.query(OutlineNode).filter(OutlineNode.id == parent_id, OutlineNode.project_id == project_id).first()
        if parent:
            char_str = f" [角色: {', '.join(lc.character.name for lc in parent.linked_characters if lc.character)}]" if parent.linked_characters else ""
            parent_context = f"父节点: [{parent.node_type}] {parent.title}{char_str}\n摘要: {parent.summary or '无'}"

    # Characters list
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.created_at.desc())
        .limit(30)
        .all()
    )
    char_lines = [f"- {c.name}（{c.role_type or '未设定'}）" for c in characters] if characters else []
    existing_characters = "\n".join(char_lines) if char_lines else "暂无角色。"

    style_ctx = build_style_context(project, include_anti_ai=False)
    from ....services.context_builders import _build_world_context as _wc
    world_ctx = _wc(db, project_id)

    messages = build_outline_writer_messages(
        style_context=style_ctx,
        existing_outline=existing_outline,
        world_context=world_ctx,
        existing_characters=existing_characters,
        requirements=requirements,
        parent_context=parent_context,
        batch_count=batch_count,
    )

    model = str(args.get("model") or "") or None
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.7,
            max_tokens=4000,
            timeout=120,
            retry=1,
            tools=[OUTLINE_NODES_TOOL],
            tool_choice="required",
        )
    except Exception as exc:
        return {"tool": "outline_writer", "status": "error", "detail": f"大纲生成失败: {exc}", "data": {}}

    tool_calls = result.get("tool_calls") or []
    if not tool_calls:
        return {
            "tool": "outline_writer",
            "status": "error",
            "detail": "大纲生成结果解析失败",
            "data": {"raw": str(result.get("content", ""))[:500]},
        }

    try:
        parsed = _json.loads(tool_calls[0]["function"]["arguments"])
    except (_json.JSONDecodeError, AttributeError):
        return {
            "tool": "outline_writer",
            "status": "error",
            "detail": "大纲生成结果解析失败",
            "data": {"raw": tool_calls[0].get("function", {}).get("arguments", "")[:500]},
        }

    if not parsed.get("nodes"):
        return {
            "tool": "outline_writer",
            "status": "error",
            "detail": "大纲生成结果解析失败",
            "data": {"raw": _json.dumps(parsed, ensure_ascii=False)[:500]},
        }

    nodes_data = parsed.get("nodes", [])
    return {
        "tool": "outline_writer",
        "status": "ok",
        "detail": f"已生成 {len(nodes_data)} 个大纲节点",
        "data": {"nodes": nodes_data, "design_notes": parsed.get("design_notes", "")},
    }
