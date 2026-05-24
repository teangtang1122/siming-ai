"""Chapter Writer workspace tool — generates chapter body prose with full writing rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....database.models import Character, CharacterRelationship, Chapter, Project
from ....prompts.chapter_writer_prompts import build_chapter_writer_messages
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
    _build_world_context,
)
from ....prompts.style_prompts import build_style_context


async def chapter_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Generate chapter body text with complete writing rules.

    Args:
        outline_node_id: Target outline node (required)
        requirements: Optional writing requirements / direction
        involved_characters: Optional list of character names appearing in this chapter
        previous_plot: Optional plot design JSON from design_plot tool
        previous_roleplay: Optional roleplay results from roleplay/dialogue_battle tools

    Returns:
        {"tool": "chapter_writer", "status": "ok", "detail": "...",
         "data": {"content": "...", "word_count": N}}
    """
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()
    involved_names: list[str] = (
        [str(n).strip() for n in args.get("involved_characters", []) if n]
        if isinstance(args.get("involved_characters"), list)
        else []
    )
    plot_design = args.get("previous_plot")
    roleplay_results = args.get("previous_roleplay")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "chapter_writer", "status": "skipped", "detail": "项目不存在", "data": {}}

    # Context: outline
    outline_ctx = _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    # Context: worldbuilding
    world_ctx = _build_world_context(db, project_id, outline_node_id)
    # Context: recent summaries
    summaries = _build_recent_summaries(db, project_id, limit=5)
    # Context: style
    style_ctx = build_style_context(project, include_anti_ai=False)

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

    messages = build_chapter_writer_messages(
        style_context=style_ctx,
        outline_context=outline_ctx,
        world_context=world_ctx,
        character_profiles=char_detail_text,
        recent_summaries=summaries,
        plot_design=plot_design,
        roleplay_results=roleplay_results,
        requirements=requirements,
    )

    model = str(args.get("model") or "") or None

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.8,
            max_tokens=6000,
            timeout=180,
            retry=1,
        )
    except Exception as exc:
        return {
            "tool": "chapter_writer",
            "status": "error",
            "detail": f"章节正文生成失败: {exc}",
            "data": {},
        }

    content = (result.get("content") or "").strip()
    if not content:
        return {
            "tool": "chapter_writer",
            "status": "error",
            "detail": "生成的章节正文为空",
            "data": {},
        }

    return {
        "tool": "chapter_writer",
        "status": "ok",
        "detail": f"已生成章节正文（{len(content)} 字）",
        "data": {
            "content": content,
            "word_count": len(content),
            "model": result.get("model", ""),
        },
    }
