"""Plot design workspace tool."""
from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ....core.json_repair import parse_json_object
from ....database.models import (
    Character,
    CharacterRelationship,
    Chapter,
    ChapterCharacter,
    Project,
    WorldbuildingEntry,
)
from ....prompts.plot_prompts import build_plot_design_messages
from ....services.context_builders import (
    _build_outline_context,
    _build_outline_overview,
    _build_recent_summaries,
    _build_scene_characters_context,
    _build_world_context,
)
from ....prompts.style_prompts import build_style_context

PLOT_DESIGN_TOOL = {
    "type": "function",
    "function": {
        "name": "design_plot_output",
        "description": "提交本章节的完整剧情设计方案，包含场景拆解、角色行为、冲突张力、情绪曲线等。",
        "parameters": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"},
                            "time": {"type": "string"},
                            "characters": {"type": "array", "items": {"type": "string"}},
                            "core_event": {"type": "string"},
                            "goal": {"type": "string"},
                            "dialogue_direction": {"type": "string"},
                        },
                        "required": ["location", "time", "characters", "core_event", "goal"],
                    },
                },
                "character_actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "character_name": {"type": "string"},
                            "motivation": {"type": "string"},
                            "action": {"type": "string"},
                            "outcome": {"type": "string"},
                        },
                        "required": ["character_name", "motivation", "action", "outcome"],
                    },
                },
                "conflicts": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["character", "environment", "inner"]},
                        "description": {"type": "string"},
                        "escalation": {"type": "string"},
                        "stakes": {"type": "string"},
                    },
                    "required": ["type", "description"],
                },
                "emotional_arc": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string"},
                        "turning_points": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"event": {"type": "string"}, "emotion_shift": {"type": "string"}},
                                "required": ["event", "emotion_shift"],
                            },
                        },
                        "end": {"type": "string"},
                    },
                    "required": ["start", "end"],
                },
                "consistency_check": {
                    "type": "object",
                    "properties": {
                        "outline_alignment": {"type": "string"},
                        "worldbuilding_compliance": {"type": "string"},
                        "character_consistency": {"type": "string"},
                        "timeline_check": {"type": "string"},
                        "potential_issues": {"type": "string"},
                    },
                },
                "new_characters_needed": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "identity": {"type": "string"},
                            "reason": {"type": "string"},
                            "core_traits": {"type": "string"},
                            "suggested_actor": {"type": "string"},
                        },
                        "required": ["name", "identity", "reason"],
                    },
                },
                "engagement_assessment": {
                    "type": "object",
                    "properties": {
                        "hooks": {"type": "array", "items": {"type": "string"}},
                        "reader_appeal": {"type": "string"},
                        "strengthening_suggestions": {"type": "string"},
                    },
                },
                "summary": {"type": "string", "description": "本章剧情一句话总结"},
            },
            "required": ["scenes", "conflicts", "emotional_arc", "engagement_assessment", "summary"],
        },
    },
}


def _parse_plot_design_payload(raw: str) -> dict | None:
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return None
    if isinstance(parsed.get("arguments"), dict):
        parsed = parsed["arguments"]
    elif isinstance(parsed.get("arguments"), str):
        nested = parse_json_object(parsed["arguments"])
        if isinstance(nested, dict):
            parsed = nested
    for key in ("design_plot_output", "plot_design", "plot", "data"):
        nested = parsed.get(key)
        if isinstance(nested, dict):
            parsed = nested
            break
    return parsed if isinstance(parsed, dict) else None


async def design_plot(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    involved_names: list[str] = (
        [str(n).strip() for n in args.get("involved_characters", []) if n]
        if isinstance(args.get("involved_characters"), list)
        else []
    )
    requirements = str(args.get("requirements") or "").strip()
    feedback = str(args.get("feedback") or "").strip()
    previous_plot = args.get("previous_plot")  # For iteration
    previous_plot_json = _json.dumps(previous_plot, ensure_ascii=False, indent=2) if previous_plot else ""

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "design_plot", "status": "skipped", "detail": "项目不存在", "data": {}}

    # Context: outline
    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    outline_overview = _build_outline_overview(db, project_id, limit=40)

    # Context: worldbuilding
    world_ctx = _build_world_context(
        db,
        project_id,
        outline_node_id,
        query_context="\n".join([requirements, feedback, previous_plot_json if previous_plot else ""]),
    )

    # Context: recent summaries
    summaries = _build_recent_summaries(db, project_id, limit=5)

    # Context: scene characters
    scene_chars = _build_scene_characters_context(db, project_id, outline_node_id)

    # Context: style
    style_ctx = build_style_context(project)

    # Context: involved characters detail
    char_details: list[str] = []
    if involved_names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(involved_names))
            .all()
        )
        for c in characters:
            detail_parts = [
                f"【{c.name}】",
                f"  身份: {c.role_type or '未设定'}",
                f"  性格: {(c.personality or '未设定')[:300]}",
                f"  背景: {(c.background or '未设定')[:300]}",
                f"  能力: {(c.abilities or '未设定')[:200]}",
                f"  外貌: {(c.appearance or '未设定')[:150]}",
            ]
            # Relationships
            rels = (
                db.query(CharacterRelationship)
                .filter(
                    CharacterRelationship.project_id == project_id,
                    (CharacterRelationship.character_a_id == c.id)
                    | (CharacterRelationship.character_b_id == c.id),
                )
                .limit(10)
                .all()
            )
            if rels:
                all_char_ids = {r.character_a_id for r in rels} | {r.character_b_id for r in rels}
                name_map = {
                    ch.id: ch.name
                    for ch in db.query(Character).filter(Character.id.in_(all_char_ids)).all()
                }
                rel_lines = []
                for r in rels:
                    other = name_map.get(
                        r.character_b_id if r.character_a_id == c.id else r.character_a_id, "?"
                    )
                    rel_lines.append(f"    {other}: {r.relationship_type}")
                if rel_lines:
                    detail_parts.append(f"  关系:\n" + "\n".join(rel_lines))
            char_details.append("\n".join(detail_parts))
    char_detail_text = "\n\n".join(char_details) if char_details else "未指定角色。"

    # Existing chapters under this outline node (to avoid duplication)
    existing_chapters_text = "暂无已有章节。"
    if outline_node_id:
        existing = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node_id)
            .order_by(Chapter.created_at.asc())
            .all()
        )
        if existing:
            existing_chapters_text = "\n".join(
                f"- [{ch.created_at.strftime('%m-%d')}] {ch.title}: {(ch.summary.summary_text if ch.summary else ch.content or '')[:200]}"
                for ch in existing
            )

    messages = build_plot_design_messages(
        project_title=project.title,
        project_description=project.description or "",
        outline_overview=outline_overview,
        outline_ctx=outline_ctx,
        world_ctx=world_ctx,
        summaries=summaries,
        existing_chapters_text=existing_chapters_text,
        scene_chars=scene_chars,
        involved_characters_text=char_detail_text,
        style_ctx=style_ctx,
        requirements=requirements,
        feedback=feedback,
        previous_plot=previous_plot_json,
        genre_hint=project.description or "",
    )

    model = str(args.get("model") or "") or None
    temperature = float(args.get("temperature") or 0.8)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            timeout=120,
            retry=1,
            extra_body={"moshu_task_type": "planning", "moshu_project_id": project_id},
            tools=[PLOT_DESIGN_TOOL],
            tool_choice="required",
        )
    except Exception as exc:
        return {"tool": "design_plot", "status": "error", "detail": f"LLM 调用失败：{exc}", "data": {}}

    tool_calls = result.get("tool_calls") or []
    raw_for_error = str(result.get("content", ""))
    if tool_calls:
        raw_for_error = str(tool_calls[0].get("function", {}).get("arguments", ""))
    parsed = _parse_plot_design_payload(raw_for_error)
    if not parsed:
        return {
            "tool": "design_plot",
            "status": "error",
            "detail": "LLM 返回的剧情设计无法解析为JSON",
            "data": {"raw": raw_for_error[:2000]},
        }

    if not isinstance(parsed, dict):
        return {
            "tool": "design_plot",
            "status": "error",
            "detail": "LLM 返回的剧情设计无法解析为JSON",
            "data": {"raw": _json.dumps(parsed, ensure_ascii=False)[:2000]},
        }

    scenes = parsed.get("scenes", [])
    new_chars = parsed.get("new_characters_needed", [])
    issues = parsed.get("consistency_check", {}).get("potential_issues", "")

    return {
        "tool": "design_plot",
        "status": "ok",
        "detail": f"剧情设计完成：{len(scenes)} 个场景"
            + (f"，建议 {len(new_chars)} 个新角色" if new_chars else "")
            + (f"，发现潜在问题" if issues else ""),
        "data": parsed,
    }
