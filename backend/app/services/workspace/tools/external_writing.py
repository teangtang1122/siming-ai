"""External writing tools — API-free tools for external agents (Claude Code, Codex).

These tools work without any Siming model API configured. They provide
context, focused writing prompts, and draft storage for external agents that
do their own generation. Quality review remains available as a separate tool.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....core.utils import count_words


def _load_external_writing_prompt_pack(
    db: Session,
    project: Any,
    warnings: list[str],
) -> dict[str, Any] | None:
    from app.database.models import PublicPromptPack
    from app.prompts.prompt_source import get_public_chapter_quality_system_prompt
    from app.prompts.style_prompts import build_style_context

    pack = (
        db.query(PublicPromptPack)
        .filter(
            PublicPromptPack.pack_id == "chapter_writing_quality",
            PublicPromptPack.enabled == True,
        )
        .first()
    )
    if not pack:
        warnings.append("Prompt pack not found: chapter_writing_quality")
        return None
    return {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "title": pack.title,
        "system_prompt": get_public_chapter_quality_system_prompt().replace(
            "{style_context}",
            build_style_context(project, include_anti_ai=False),
        ),
    }


async def prepare_external_writing_context(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Prepare one governed, API-free context package for chapter writing."""
    from app.database.models import Chapter, OutlineNode, Project
    from app.services.context_orchestrator import ContextOrchestrator
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": "Project not found",
            "data": None,
        }

    outline_node_id = str(args.get("outline_node_id") or "").strip()
    if not outline_node_id:
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": "The Agent must select a real chapter-level outline ID before preparing writing context.",
            "data": None,
        }
    target_outline = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        )
        .first()
    )
    if not target_outline or target_outline.node_type != "chapter":
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": "outline_node_id must identify a chapter node in the current project.",
            "data": {"outline_node_id": outline_node_id},
        }
    existing_chapter = db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.outline_node_id == outline_node_id,
    ).first()
    if existing_chapter:
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": (
                "The selected outline already has a formal chapter. Chapter writing only "
                "creates independent new-chapter drafts and never overwrites saved prose."
            ),
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": existing_chapter.id,
            },
        }

    orchestrator = ContextOrchestrator(db)
    requested_manifest_id = str(args.get("context_manifest_id") or "").strip()
    if requested_manifest_id:
        manifest = orchestrator.get_manifest(requested_manifest_id, project_id)
        if not manifest:
            return {
                "tool": "prepare_external_writing_context",
                "status": "needs_confirmation",
                "detail": "The requested context manifest was not found.",
                "data": {"context_manifest_id": requested_manifest_id},
            }
    else:
        manifest = orchestrator.prepare(
            project_id=project_id,
            task_type="writing",
            model=str(args.get("model") or "") or None,
            execution_route="external_mcp",
            arguments=args,
            pinned_chunk_ids=(
                args.get("pinned_chunk_ids")
                if isinstance(args.get("pinned_chunk_ids"), list)
                else ()
            ),
            pinned_source_ids=(
                args.get("pinned_source_ids")
                if isinstance(args.get("pinned_source_ids"), list)
                else ()
            ),
        )

    manifest_payload = orchestrator.manifest_payload(manifest, include_content=False)
    if manifest.status == "blocked_rebuild":
        return {
            "tool": "prepare_external_writing_context",
            "status": "blocked_rebuild",
            "detail": "Context indexes are rebuilding. External writes are paused.",
            "data": {
                "context_manifest_id": manifest.id,
                "context_manifest_status": manifest.status,
                "context_budget": manifest_payload["budget"],
                "context_coverage": manifest_payload["coverage"],
                "warnings": manifest_payload["warnings"],
            },
        }

    warnings = list(manifest_payload["warnings"])
    prompt_pack = (
        _load_external_writing_prompt_pack(db, project, warnings)
        if args.get("include_prompt_pack", True)
        else None
    )

    status = "needs_confirmation" if manifest.status == "needs_confirmation" else "ok"
    detail = (
        f"Governed context prepared: {len(manifest_payload['items'])} selected sources, "
        f"{manifest.estimated_input_tokens}/{manifest.input_budget_tokens} input tokens"
    )
    if status == "needs_confirmation":
        detail += ". Required context is missing; author confirmation is required before generation."

    return {
        "tool": "prepare_external_writing_context",
        "status": status,
        "detail": detail,
        "data": {
            "project": {"id": project.id, "title": project.title},
            "target": {
                "outline_node_id": target_outline.id,
                "title": target_outline.title,
            },
            "requirements": str(args.get("requirements") or "").strip(),
            "prompt_pack": prompt_pack,
            "context_manifest_id": manifest.id,
            "context_manifest_status": manifest.status,
            "requires_author_confirmation": status == "needs_confirmation",
            "context_budget": manifest_payload["budget"],
            "context_coverage": manifest_payload["coverage"],
            "evidence_sources": manifest_payload["items"],
            "writing_context": manifest.rendered_context,
            "warnings": list(dict.fromkeys(warnings)),
            "workflow_boundaries": {
                "current_task": "base_chapter_writing",
                "de_ai_revision": "separate_user_action",
                "quality_review": "separate_user_action",
            },
            "next_tool_suggestions": [
                {
                    "tool": "submit_context_evidence",
                    "description": "Submit required manifest sources before saving the draft.",
                },
                {
                    "tool": "save_external_chapter_draft",
                    "description": "Save one unsaved draft and end the model turn.",
                },
            ],
        },
    }

async def save_external_chapter_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save an externally generated chapter draft.

    API-free: stores draft content server-side and returns draft_id/content_ref.
    Saving the draft is the terminal boundary for an AI chapter-writing turn.
    """
    from app.database.models import Chapter, OutlineNode
    from app.services.cataloging.launcher import (
        cataloging_block_result,
        cataloging_required_block_result,
        find_blocking_chapter_cataloging_job,
        find_cataloging_required_chapter,
    )
    from app.services.workspace.generated_drafts import (
        ChapterDraftOutlineConflict,
        PendingChapterDraftConflict,
        find_pending_chapter_draft,
        pending_draft_block_result,
        store_chapter_draft,
    )
    from app.services.workspace.turn_control import AssistantTurnDirective, apply_turn_directive

    content = str(args.get("content") or "").strip()
    if not content:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "content is required",
            "data": None,
        }

    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    context_manifest_id = str(args.get("context_manifest_id") or "").strip() or None
    source_agent = str(args.get("source_agent") or "external").strip()
    if not outline_node_id:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "The Agent must select a real chapter-level outline ID before saving a draft.",
            "data": None,
        }
    target_outline = db.query(OutlineNode).filter(
        OutlineNode.project_id == project_id,
        OutlineNode.id == outline_node_id,
    ).first()
    if not target_outline or target_outline.node_type != "chapter":
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "outline_node_id must identify a chapter node in the current project.",
            "data": {"outline_node_id": outline_node_id},
        }
    existing_chapter = db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.outline_node_id == outline_node_id,
    ).first()
    if existing_chapter:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "The selected outline already has a formal chapter. This tool cannot "
                "create a rewrite draft or overwrite saved prose."
            ),
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": existing_chapter.id,
            },
        }
    title = str(target_outline.title or "").strip()

    pending_draft = find_pending_chapter_draft(db, project_id)
    if pending_draft:
        return pending_draft_block_result("save_external_chapter_draft", pending_draft)
    blocking_job = find_blocking_chapter_cataloging_job(
        db,
        project_id,
    )
    if blocking_job:
        return cataloging_block_result("save_external_chapter_draft", blocking_job)
    required_chapter = find_cataloging_required_chapter(
        db,
        project_id,
    )
    if required_chapter:
        return cataloging_required_block_result("save_external_chapter_draft", required_chapter)

    if str(args.get("_context_execution_route") or "").strip() in {"external_mcp", "local_cli_agent"}:
        if not context_manifest_id:
            return {
                "tool": "save_external_chapter_draft",
                "status": "needs_confirmation",
                "detail": "Prepare task context first and attach its context_manifest_id to the draft.",
                "data": None,
            }
        from ....services.context_orchestrator import ContextOrchestrator

        context_manifest = ContextOrchestrator(db).get_manifest(context_manifest_id, project_id)
        if not context_manifest:
            return {
                "tool": "save_external_chapter_draft",
                "status": "needs_confirmation",
                "detail": "The supplied context manifest is unavailable for this project.",
                "data": {"context_manifest_id": context_manifest_id},
            }
        manifest_outline_ids = {
            str(item.source_id)
            for item in context_manifest.items
            if item.category == "target_outline" and item.source_id
        }
        if outline_node_id not in manifest_outline_ids:
            return {
                "tool": "save_external_chapter_draft",
                "status": "needs_confirmation",
                "detail": "The context manifest target does not match the selected chapter outline.",
                "data": {
                    "context_manifest_id": context_manifest_id,
                    "outline_node_id": outline_node_id,
                },
            }

    try:
        draft_id = store_chapter_draft(
            project_id=project_id,
            content=content,
            title=title,
            outline_node_id=outline_node_id,
            context_manifest_id=context_manifest_id,
            db=db,
        )
    except PendingChapterDraftConflict as conflict:
        return pending_draft_block_result("save_external_chapter_draft", conflict.draft)
    except ChapterDraftOutlineConflict as conflict:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "The outline acquired a formal chapter before this draft was stored. "
                "The late result was discarded without replacing prose or creating a stale draft."
            ),
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": conflict.chapter.id,
            },
        }

    return apply_turn_directive({
        "tool": "save_external_chapter_draft",
        "status": "ok",
        "detail": f"章节草稿已生成（{count_words(content)} 字），尚未保存；本轮必须结束",
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "title": title,
            "outline_node_id": outline_node_id,
            "context_manifest_id": context_manifest_id,
            "content": content,
            "draft_status": "pending",
            "next_actions": ["save_and_catalog", "save_only"],
            "word_count": count_words(content),
            "source_agent": source_agent,
        },
    }, AssistantTurnDirective.END_AFTER_DRAFT)


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
