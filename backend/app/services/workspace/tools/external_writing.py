"""External writing tools — API-free tools for external agents (Claude Code, Codex).

These tools work without any Siming model API configured. They provide
context, focused writing prompts, and draft storage for external agents that
do their own generation. Quality review remains available as a separate tool.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....core.utils import count_words
from ...character_archive import character_archive_payload


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
    - Warnings and next tool suggestions

    De-AI rewriting and quality review are deliberately omitted from this base
    writing package so one writing task does not silently run multiple phases.
    """
    from app.database.models import (
        Project, Chapter, OutlineNode,
        Character, CharacterRelationship, WorldbuildingEntry,
        PublicPromptPack,
    )
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    outline_node_id = str(args.get("outline_node_id") or "").strip()
    requested_mode = str(args.get("mode") or "quality").strip() or "quality"
    mode = requested_mode if requested_mode in {"fast", "quality"} else "quality"
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

    # The compatibility response below is now backed by one auditable baseline
    # manifest. External agents may inspect the read-only mirror, but formal
    # writes must later prove selected evidence against this baseline.
    from app.services.context_orchestrator import ContextOrchestrator

    context_orchestrator = ContextOrchestrator(db)
    requested_manifest_id = str(args.get("context_manifest_id") or "").strip()
    if requested_manifest_id:
        context_manifest = context_orchestrator.get_manifest(requested_manifest_id, project_id)
        if not context_manifest:
            return {
                "tool": "prepare_external_writing_context",
                "status": "needs_confirmation",
                "detail": "The requested context manifest was not found.",
                "data": {"context_manifest_id": requested_manifest_id},
            }
    else:
        context_manifest = context_orchestrator.prepare(
            project_id=project_id,
            task_type="writing",
            model=str(args.get("model") or "") or None,
            execution_route="external_mcp",
            arguments=args,
            pinned_chunk_ids=args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else (),
            pinned_source_ids=args.get("pinned_source_ids") if isinstance(args.get("pinned_source_ids"), list) else (),
        )
    if context_manifest.status == "blocked_rebuild":
        return {
            "tool": "prepare_external_writing_context",
            "status": "blocked_rebuild",
            "detail": "Context indexes are rebuilding. Browsing remains available, but external writes are paused.",
            "data": {
                "context_manifest_id": context_manifest.id,
                "context_manifest": context_orchestrator.manifest_payload(context_manifest, include_content=False),
            },
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
        "requested_mode": requested_mode,
        "effective_mode": mode,
        "requirements": requirements,
        "warnings": [],
        "next_tool_suggestions": [],
    }
    if requested_mode != mode:
        result["warnings"].append(
            f"Requested mode '{requested_mode}' was normalized to quality. "
            "Supported writing modes are fast and quality."
        )

    # Base-writing guardrails only. Analysis/review prompts are loaded by their
    # own explicit tasks instead of being bundled into every writing request.
    from app.prompts.prompt_source import get_api_free_mode_rules

    result["api_free_mode_rules"] = get_api_free_mode_rules()
    result["workflow_boundaries"] = {
        "current_task": "base_chapter_writing",
        "de_ai_revision": "separate_user_action",
        "quality_review": "separate_user_action",
    }

    # Prompt pack — build system_prompt from shared source (same modules as internal packs)
    if include_prompt_pack:
        pack_id = "chapter_writing_fast" if mode == "fast" else "chapter_writing_quality"
        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()
        if pack:
            from app.prompts.prompt_source import (
                get_public_chapter_fast_system_prompt,
                get_public_chapter_quality_system_prompt,
            )
            from app.prompts.style_prompts import build_style_context
            # Build style context for this project
            style_ctx = build_style_context(project, include_anti_ai=False)
            # Get system prompt from shared source and inject style context
            prompt_builder = (
                get_public_chapter_fast_system_prompt
                if mode == "fast"
                else get_public_chapter_quality_system_prompt
            )
            system_prompt = prompt_builder()
            system_prompt = system_prompt.replace("{style_context}", style_ctx)
            result["prompt_pack"] = {
                "pack_id": pack.pack_id,
                "requested_mode": requested_mode,
                "effective_mode": mode,
                "version": pack.version,
                "title": pack.title,
                "system_prompt": system_prompt,
                "workflow": pack.workflow_json,
                "quality_rubric": None,
                "forbidden_patterns": [],
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
            chapter_node = node
            if node.node_type == "section" and node.parent_id:
                parent = db.query(OutlineNode).filter(
                    OutlineNode.project_id == project_id,
                    OutlineNode.id == node.parent_id,
                ).first()
                if parent:
                    chapter_node = parent
            sections = db.query(OutlineNode).filter(
                OutlineNode.project_id == project_id,
                OutlineNode.parent_id == chapter_node.id,
                OutlineNode.node_type == "section",
            ).order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc()).limit(8).all()
            result["outline"] = {
                "id": node.id,
                "title": node.title,
                "summary": node.summary,
                "actual_summary": node.actual_summary,
                "planned_summary": node.planned_summary,
                "node_type": node.node_type,
                "status": node.status,
                "chapter_node": {
                    "id": chapter_node.id,
                    "title": chapter_node.title,
                    "summary": chapter_node.summary,
                    "actual_summary": chapter_node.actual_summary,
                    "planned_summary": chapter_node.planned_summary,
                    "node_type": chapter_node.node_type,
                } if chapter_node.id != node.id else None,
                "section_nodes": [
                    {
                        "id": section.id,
                        "title": section.title,
                        "summary": section.summary,
                        "actual_summary": section.actual_summary,
                        "planned_summary": section.planned_summary,
                        "is_current": section.id == node.id,
                    }
                    for section in sections
                ],
            }
        else:
            result["warnings"].append(f"Outline node not found: {outline_node_id}")

    # Recent chapter summaries
    recent_chapters = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).order_by(Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc()).limit(5).all()

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
    characters = db.query(Character).filter(
        Character.project_id == project_id,
        or_(Character.role_type.is_(None), Character.role_type != "merged_alias"),
    ).limit(16).all()

    result["characters"] = []
    character_cards_by_id: dict[str, dict[str, Any]] = {}
    for c in characters:
        card = character_archive_payload(c)
        state = card.pop("state")
        flattened_card = {**card, **state}
        character_cards_by_id[str(c.id)] = flattened_card
        result["characters"].append(flattened_card)

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

    # Compatibility keys remain present, but de-AI rules and the review rubric
    # are not injected into ordinary writing.
    result["forbidden_patterns"] = []
    result["quality_rubric"] = None
    result["rhetoric_guidelines"] = (project.rhetoric_guidelines or "").strip()

    # Full base-writing style context — same as what internal chapter_writer gets.
    from app.prompts.style_prompts import build_style_context
    result["style_context"] = build_style_context(project, include_anti_ai=False)

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
        ).order_by(AssistantMemory.importance.desc()).limit(15).all()
        if memories:
            result["memories"] = [
                {"key": m.key, "value": m.value, "category": m.category}
                for m in memories
            ]
    except Exception:
        pass  # Memory injection is best-effort

    # Focused base-writing flow. De-AI and quality review are separate explicit
    # actions and therefore are not suggested as part of this task.
    result["next_tool_suggestions"] = [
        {"tool": "recall", "description": "查询已有记忆，避免重复或矛盾"},
        {"tool": "save_external_chapter_draft", "description": "保存生成的草稿"},
        {"tool": "create_chapter", "description": "用 draft_id 和 skip_style_repair=true 正式保存；检查自动创建的 cataloging_job 与 next_action"},
    ]

    # Replace the old fixed "16 characters + 20 worldbuilding" mirror with
    # the same bounded selection used by internal API and CLI execution.
    selected_items = context_orchestrator.manifest_payload(context_manifest, include_content=True)["items"]
    result["context_manifest_id"] = context_manifest.id
    result["manifest_id"] = context_manifest.id
    result["context_manifest_status"] = context_manifest.status
    result["requires_author_confirmation"] = context_manifest.status == "needs_confirmation"
    result["context_manifest"] = context_orchestrator.manifest_payload(context_manifest, include_content=True)
    result["selected_context"] = selected_items
    selected_character_ids = {
        str(item["source_id"])
        for item in selected_items
        if item["source_type"] == "character" and item.get("source_id")
    }
    if selected_character_ids:
        selected_character_rows = db.query(Character).filter(
            Character.project_id == project_id,
            Character.id.in_(selected_character_ids),
        ).all()
        for character in selected_character_rows:
            card = character_archive_payload(character)
            state = card.pop("state")
            character_cards_by_id[str(character.id)] = {**card, **state}

    result["characters"] = [
        {
            **character_cards_by_id.get(str(item["source_id"]), {}),
            "id": item["source_id"],
            "name": item["title"],
            "context": item["content"],
            "source_hash": item["source_hash"],
        }
        for item in selected_items
        if item["source_type"] == "character"
    ]
    if selected_character_ids:
        relationships = db.query(CharacterRelationship).filter(
            CharacterRelationship.project_id == project_id,
            or_(
                CharacterRelationship.character_a_id.in_(selected_character_ids),
                CharacterRelationship.character_b_id.in_(selected_character_ids),
            ),
        ).all()
        relationship_character_ids = {
            endpoint
            for relationship in relationships
            for endpoint in (relationship.character_a_id, relationship.character_b_id)
            if endpoint
        }
        relationship_names = dict(
            db.query(Character.id, Character.name)
            .filter(Character.id.in_(relationship_character_ids))
            .all()
        )
        result["relationships"] = [
            {
                "source_id": relationship.character_a_id,
                "source_name": relationship_names.get(
                    relationship.character_a_id,
                    relationship.character_a_id,
                ),
                "target_id": relationship.character_b_id,
                "target_name": relationship_names.get(
                    relationship.character_b_id,
                    relationship.character_b_id,
                ),
                "relationship_type": relationship.relationship_type,
                "description": relationship.description or "",
            }
            for relationship in relationships
        ]
    result["worldbuilding"] = [
        {
            "id": item["source_id"],
            "title": item["title"],
            "content": item["content"],
            "source_hash": item["source_hash"],
        }
        for item in selected_items
        if item["source_type"] == "worldbuilding"
    ]
    result["recent_summaries"] = [
        {
            "id": item["source_id"],
            "title": item["title"],
            "summary": item["content"],
            "source_hash": item["source_hash"],
        }
        for item in selected_items
        if item["source_type"] == "chapter_summary"
    ]
    result["warnings"] = list(dict.fromkeys([*(result.get("warnings") or []), *(context_manifest.warnings_json or [])]))
    result["next_tool_suggestions"] = [
        {"tool": "submit_context_evidence", "description": "Submit selected baseline or task-search sources before formal write."},
        *result["next_tool_suggestions"],
    ]
    # This endpoint is an API-free context mirror, so it remains successful for
    # old clients even when the baseline needs author confirmation. The later
    # generated draft/create/archive write gate enforces that confirmation.
    status = "ok"
    detail = (
        f"Governed context prepared: {len(selected_items)} selected sources, "
        f"{context_manifest.estimated_input_tokens}/{context_manifest.input_budget_tokens} input tokens"
    )
    if context_manifest.status == "needs_confirmation":
        detail += ". Required anchors are missing; author confirmation or override is required before generation."
    return {
        "tool": "prepare_external_writing_context",
        "status": status,
        "detail": detail,
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
    context_manifest_id = str(args.get("context_manifest_id") or "").strip() or None
    source_agent = str(args.get("source_agent") or "external").strip()
    if str(args.get("_context_execution_route") or "").strip() in {"external_mcp", "local_cli_agent"}:
        if not context_manifest_id:
            return {
                "tool": "save_external_chapter_draft",
                "status": "needs_confirmation",
                "detail": "Prepare task context first and attach its context_manifest_id to the draft.",
                "data": None,
            }
        from ....services.context_orchestrator import ContextOrchestrator

        if not ContextOrchestrator(db).get_manifest(context_manifest_id, project_id):
            return {
                "tool": "save_external_chapter_draft",
                "status": "needs_confirmation",
                "detail": "The supplied context manifest is unavailable for this project.",
                "data": {"context_manifest_id": context_manifest_id},
            }

    draft_id = store_chapter_draft(
        project_id=project_id,
        content=content,
        title=title,
        outline_node_id=outline_node_id,
        context_manifest_id=context_manifest_id,
        db=db,
    )

    return {
        "tool": "save_external_chapter_draft",
        "status": "ok",
        "detail": f"Draft saved: {count_words(content)} words",
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "title": title,
            "outline_node_id": outline_node_id,
            "context_manifest_id": context_manifest_id,
            "word_count": count_words(content),
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
        "detail": f"Draft retrieved: {count_words(content)} words",
        "data": {
            "draft_id": draft_id,
            "content": content,
            "word_count": count_words(content),
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
            if isinstance(scores, dict) and scores:
                from app.services.narrative_governance import record_quality_metric

                aliases = {
                    "plot_tension": ("plot_tension", "plot", "情节张力", "情节推进"),
                    "emotional_tension": ("emotional_tension", "emotion", "情绪张力"),
                    "pacing_density": ("pacing_density", "pacing", "节奏", "节奏控制"),
                    "character_consistency": ("character_consistency", "character", "角色一致性", "角色塑造"),
                    "viewpoint_consistency": ("viewpoint_consistency", "viewpoint", "视角一致性"),
                    "world_consistency": ("world_consistency", "world", "设定一致性", "世界观一致性"),
                }
                metric = {"chapter_id": chapter_id, "passed": bool(passed), "warnings": list(issues or []), "evidence": "；".join(str(item) for item in suggestions[:10]), "source": "external_agent"}
                for target, names in aliases.items():
                    value = next((scores[name] for name in names if isinstance(scores.get(name), (int, float))), None)
                    if value is not None:
                        metric[target] = float(value) * 10 if float(value) <= 10 else float(value)
                quality_row = record_quality_metric(db, project_id, metric)
                review["quality_metric_id"] = quality_row.id

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
