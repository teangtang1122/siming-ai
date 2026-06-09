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
        },
        "warnings": [],
        "next_tool_suggestions": [],
    }

    # Prompt pack
    if include_prompt_pack:
        pack_id = "chapter_writing_quality" if mode == "quality" else "chapter_writing_fast"
        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()
        if pack:
            result["prompt_pack"] = {
                "pack_id": pack.pack_id,
                "version": pack.version,
                "title": pack.title,
                "system_prompt": pack.system_prompt,
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

    # Characters
    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).limit(16).all()

    result["characters"] = []
    for c in characters:
        result["characters"].append({
            "id": c.id,
            "name": c.name,
            "role_type": c.role_type,
            "personality": c.personality,
            "current_location": c.current_location,
            "current_goal": c.current_goal,
            "life_status": c.life_status,
        })

    # Relationships
    try:
        if characters:
            char_ids = [c.id for c in characters]
            rels = db.query(CharacterRelationship).filter(
                CharacterRelationship.source_id.in_(char_ids),
            ).all()
            result["relationships"] = [
                {
                    "source_id": r.source_id,
                    "target_id": r.target_id,
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
            "content": e.content[:500],
        }
        for e in wb_entries
    ]

    # Forbidden patterns from project settings
    if project.forbidden_sentence_patterns:
        patterns = [p.strip() for p in project.forbidden_sentence_patterns.split("\n") if p.strip()]
        result["forbidden_patterns"] = patterns

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

    # Next tool suggestions
    result["next_tool_suggestions"] = [
        {"tool": "save_external_chapter_draft", "description": "Save generated draft before creating chapter"},
        {"tool": "record_external_quality_review", "description": "Record quality review of the draft"},
        {"tool": "create_chapter", "description": "Create chapter from draft using draft_id/content_ref"},
        {"tool": "apply_external_story_updates", "description": "Apply character/worldbuilding updates after writing"},
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

    draft = get_chapter_draft(draft_id)
    if not draft:
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": f"Draft not found: {draft_id}",
            "data": None,
        }

    # Verify project ownership
    if draft.get("project_id") != project_id:
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": "Draft does not belong to this project",
            "data": None,
        }

    return {
        "tool": "get_external_chapter_draft",
        "status": "ok",
        "detail": f"Draft: {draft.get('title', 'Untitled')}",
        "data": {
            "draft_id": draft_id,
            "title": draft.get("title"),
            "content": draft.get("content"),
            "outline_node_id": draft.get("outline_node_id"),
            "word_count": len(draft.get("content", "")),
        },
    }
