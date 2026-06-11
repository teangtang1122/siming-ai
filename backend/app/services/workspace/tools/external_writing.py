"""External writing tools — API-free tools for external agents (Claude Code, Codex).

These tools work without any Moshu model API configured. They provide
context, prompt packs, draft storage, and quality review recording
for external agents that do their own generation.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


async def prepare_external_writing_context(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Build a complete writing context package for external agents.

    This tool is API-free — it does NOT call LLMGateway. It assembles
    everything an external agent needs to write a chapter:
    - Prompt pack (writing methodology)
    - Context sections (outline, characters, worldbuilding, summaries)
    - Quality rubric
    - Forbidden patterns
    - Warnings and next tool suggestions
    """
    from app.database.models import (
        Project, Chapter, ChapterSummary, OutlineNode,
        Character, CharacterRelationship, WorldbuildingEntry,
        PublicPromptPack,
    )
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    outline_node_id = str(args.get("outline_node_id") or "").strip()
    mode = str(args.get("mode") or "quality").strip()
    include_prompt_pack = args.get("include_prompt_pack", True)
    requirements = str(args.get("requirements") or "").strip()

    # Get project
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": "Project not found",
            "data": None,
        }

    result: dict[str, Any] = {
        "project": {
            "id": project.id,
            "title": project.title,
            "writing_style": project.writing_style,
            "forbidden_sentence_patterns": project.forbidden_sentence_patterns,
            "narrative_perspective": project.narrative_perspective,
            "rhetoric_guidelines": project.rhetoric_guidelines,
            "short_sentences": project.short_sentences,
            "custom_style_prompt": project.custom_style_prompt,
        },
        "mode": mode,
        "requirements": requirements,
        "warnings": [],
        "next_tool_suggestions": [],
    }

    # Prompt pack — build system_prompt from shared source (same modules as internal packs)
    if include_prompt_pack:
        pack_id = "chapter_writing_quality" if mode == "quality" else "chapter_writing_fast"
        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()
        if pack:
            from app.prompts.prompt_source import (
                get_public_chapter_quality_system_prompt,
                get_public_chapter_fast_system_prompt,
            )
            from app.prompts.style_prompts import build_style_context
            # Build style context for this project
            style_ctx = build_style_context(project, include_anti_ai=True)
            # Get system prompt from shared source and inject style context
            if mode == "quality":
                system_prompt = get_public_chapter_quality_system_prompt()
            else:
                system_prompt = get_public_chapter_fast_system_prompt()
            system_prompt = system_prompt.replace("{style_context}", style_ctx)
            result["prompt_pack"] = {
                "pack_id": pack.pack_id,
                "version": pack.version,
                "title": pack.title,
                "system_prompt": system_prompt,
                "workflow": pack.workflow_json,
                "quality_rubric": pack.quality_rubric_json,
                "forbidden_patterns": pack.forbidden_patterns_json,
            }
        else:
            result["warnings"].append(f"Prompt pack not found: {pack_id}")

    # Outline
    if outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        ).first()
        if node:
            result["outline"] = {
                "id": node.id,
                "title": node.title,
                "summary": node.summary,
                "node_type": node.node_type,
                "status": node.status,
            }
        else:
            result["warnings"].append(f"Outline node not found: {outline_node_id}")

    # Recent chapter summaries
    recent_chapters = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).order_by(Chapter.created_at.desc()).limit(5).all()

    result["recent_summaries"] = []
    for ch in recent_chapters:
        summary_text = ch.summary.summary_text if ch.summary else None
        result["recent_summaries"].append({
            "id": ch.id,
            "title": ch.title,
            "summary": summary_text,
            "word_count": ch.word_count,
        })

    # Characters — full state fields (same as internal assistant sees)
    from app.database.models import CharacterAlias
    characters = db.query(Character).filter(
        Character.project_id == project_id,
        Character.role_type != "merged_alias",
    ).limit(16).all()

    result["characters"] = []
    for c in characters:
        # Resolve aliases
        aliases = [a.alias for a in (c.aliases or []) if a.alias]
        result["characters"].append({
            "id": c.id,
            "name": c.name,
            "aliases": aliases,
            "role_type": c.role_type,
            "appearance": c.appearance or "",
            "personality": c.personality or "",
            "background": (c.background or "")[:2000],
            "current_location": c.current_location or "",
            "current_goal": c.current_goal or "",
            "life_status": c.life_status or "",
            "realm_or_level": c.realm_or_level or "",
            "physical_state": c.physical_state or "",
            "mental_state": c.mental_state or "",
            "active_conflict": c.active_conflict or "",
            "abilities_state": c.abilities_state or "",
            "items_or_assets": c.items_or_assets or "",
        })

    # Relationships
    try:
        if characters:
            char_ids = [c.id for c in characters]
            rels = db.query(CharacterRelationship).filter(
                CharacterRelationship.character_a_id.in_(char_ids),
            ).all()
            result["relationships"] = [
                {
                    "source_id": r.character_a_id,
                    "target_id": r.character_b_id,
                    "relationship_type": r.relationship_type,
                    "description": r.description,
                }
                for r in rels
            ]
    except Exception:
        result["relationships"] = []

    # Worldbuilding
    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).limit(20).all()

    result["worldbuilding"] = [
        {
            "id": e.id,
            "title": e.title,
            "dimension": e.dimension,
            "content": e.content[:1500],
        }
        for e in wb_entries
    ]

    # Merged forbidden patterns (system defaults + project overrides)
    from app.prompts.style_prompts import effective_forbidden_patterns, effective_rhetoric_guidelines
    merged_patterns = effective_forbidden_patterns(project)
    result["forbidden_patterns"] = [p.strip() for p in merged_patterns.splitlines() if p.strip()]

    # Rhetoric guidelines (system defaults + project overrides)
    result["rhetoric_guidelines"] = effective_rhetoric_guidelines(project)

    # Full style context — same as what internal chapter_writer gets
    from app.prompts.style_prompts import build_style_context
    result["style_context"] = build_style_context(project, include_anti_ai=True)

    # Quality rubric (from prompt pack)
    if include_prompt_pack and "prompt_pack" in result:
        rubric = result["prompt_pack"].get("quality_rubric")
        if rubric:
            result["quality_rubric"] = rubric

    # Warnings
    if not result["recent_summaries"]:
        result["warnings"].append("No previous chapters found. This may be the first chapter.")
    if not result["characters"]:
        result["warnings"].append("No characters found. Consider creating characters first.")
    if not result["worldbuilding"]:
        result["warnings"].append("No worldbuilding entries found.")
    if outline_node_id and "outline" not in result:
        result["warnings"].append("Outline node not found. Writing without outline context.")

    # Auto-match relevant skills (same as internal assistant)
    try:
        from app.services.skills.service import select_relevant_skills, build_skill_prompt_section
        message_for_skills = requirements or (f"写第{outline_node_id}章" if outline_node_id else "写章节")
        matched_skills = select_relevant_skills(db, project_id, message_for_skills, "project")
        skill_prompt_section, skill_info = build_skill_prompt_section(matched_skills)
        if skill_prompt_section:
            result["matched_skills"] = skill_info
            result["skill_instructions"] = skill_prompt_section
    except Exception:
        pass  # Skill matching is best-effort

    # Auto-inject relevant memories
    try:
        from app.database.models import AssistantMemory
        memories = db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.importance >= 7,
        ).order_by(AssistantMemory.importance.desc()).limit(5).all()
        if memories:
            result["memories"] = [
                {"key": m.key, "value": m.value, "category": m.category}
                for m in memories
            ]
    except Exception:
        pass  # Memory injection is best-effort

    # Next tool suggestions — mirrors internal assistant's post-writing flow
    result["next_tool_suggestions"] = [
        {"tool": "recall", "description": "查询已有记忆，避免重复或矛盾"},
        {"tool": "save_external_chapter_draft", "description": "保存生成的草稿"},
        {"tool": "record_external_quality_review", "description": "记录质量自评"},
        {"tool": "create_chapter", "description": "用 draft_id 保存章节"},
        {"tool": "detect_character_changes", "description": "检测角色状态变化"},
        {"tool": "detect_new_worldbuilding", "description": "检测新增世界观元素"},
        {"tool": "apply_external_story_updates", "description": "应用角色/世界观更新"},
        {"tool": "evaluate_chapter", "description": "8维度80分质量评估（需要墨枢API）"},
    ]

    return {
        "tool": "prepare_external_writing_context",
        "status": "ok",
        "detail": f"Context prepared: {len(result['characters'])} characters, {len(result['worldbuilding'])} worldbuilding, {len(result['recent_summaries'])} recent chapters",
        "data": result,
    }


async def save_external_chapter_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save an externally generated chapter draft.

    API-free: stores draft content server-side and returns draft_id/content_ref.
    The draft can later be passed to create_chapter via draft_id.
    """
    from app.services.workspace.generated_drafts import store_chapter_draft

    content = str(args.get("content") or "").strip()
    if not content:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "content is required",
            "data": None,
        }

    title = str(args.get("title") or "").strip()
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    source_agent = str(args.get("source_agent") or "external").strip()
    quality_review_json = args.get("quality_review_json")

    draft_id = store_chapter_draft(
        project_id=project_id,
        content=content,
        title=title,
        outline_node_id=outline_node_id,
        db=db,
    )

    return {
        "tool": "save_external_chapter_draft",
        "status": "ok",
        "detail": f"Draft saved: {len(content)} chars",
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "title": title,
            "word_count": len(content),
            "source_agent": source_agent,
        },
    }


async def get_external_chapter_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Get a saved chapter draft by ID.

    API-free: reads from draft storage.
    """
    from app.services.workspace.generated_drafts import get_chapter_draft

    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip()
    if not draft_id:
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": "draft_id is required",
            "data": None,
        }

    draft_content = get_chapter_draft(project_id, draft_id)
    if not draft_content:
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": f"Draft not found: {draft_id}",
            "data": None,
        }

    # get_chapter_draft returns the content string directly
    content = draft_content if isinstance(draft_content, str) else str(draft_content)

    return {
        "tool": "get_external_chapter_draft",
        "status": "ok",
        "detail": f"Draft retrieved: {len(content)} chars",
        "data": {
            "draft_id": draft_id,
            "content": content,
            "word_count": len(content),
        },
    }


async def record_external_quality_review(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Record a quality review from an external agent.

    API-free: stores review metadata without calling LLM.
    """
    from app.database.models import Chapter
    from app.services.workspace.generated_drafts import get_chapter_draft
    import json as _json

    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip()
    chapter_id = str(args.get("chapter_id") or "").strip()
    scores = args.get("scores", {})
    issues = args.get("issues", [])
    suggestions = args.get("revision_suggestions", [])
    passed = args.get("pass", True)
    reviewer_model = str(args.get("reviewer_model") or "external").strip()
    prompt_pack_version = str(args.get("prompt_pack_version") or "").strip()

    # Validate input
    if not draft_id and not chapter_id:
        return {
            "tool": "record_external_quality_review",
            "status": "skipped",
            "detail": "draft_id or chapter_id is required",
            "data": None,
        }

    # Build review record
    review = {
        "scores": scores,
        "issues": issues[:20],
        "revision_suggestions": suggestions[:20],
        "pass": passed,
        "reviewer_model": reviewer_model,
        "prompt_pack_version": prompt_pack_version,
        "source": "external_agent",
    }

    # Calculate total score if scores provided
    if isinstance(scores, dict) and scores:
        total = sum(v for v in scores.values() if isinstance(v, (int, float)))
        review["total_score"] = total
        review["max_score"] = len(scores) * 10 if scores else 0

    # Try to attach to chapter if chapter_id provided
    if chapter_id:
        chapter = db.query(Chapter).filter(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        ).first()
        if chapter:
            review["chapter_id"] = chapter_id
            review["chapter_title"] = chapter.title

    # Try to get draft info
    if draft_id:
        try:
            draft_content = get_chapter_draft(project_id, draft_id)
            if draft_content:
                review["draft_id"] = draft_id
                review["draft_content_length"] = len(draft_content) if isinstance(draft_content, str) else 0
        except Exception:
            pass  # Draft lookup is optional

    return {
        "tool": "record_external_quality_review",
        "status": "ok",
        "detail": f"Review recorded: {'PASS' if passed else 'FAIL'}" + (f" (total: {review.get('total_score', '?')})" if scores else ""),
        "data": review,
    }
