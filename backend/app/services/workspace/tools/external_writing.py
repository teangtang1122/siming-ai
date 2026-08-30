"""External writing tools — API-free tools for external agents (Claude Code, Codex).

These tools work without any Siming model API configured. They provide
context, focused writing prompts, and draft storage for external agents that
do their own generation. Quality review remains available as a separate tool.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....core.utils import count_words
from ....services.task_context_selection import (
    TASK_CONTEXT_SOFT_TARGET_TOKENS,
    render_generation_context,
)


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


def _external_writing_context_result(
    db: Session,
    project: Any,
    target_outline: Any,
    target_chapter: Any | None,
    manifest: Any,
    manifest_payload: dict[str, Any],
    args: dict[str, Any],
) -> dict[str, Any]:
    warnings = list(manifest_payload["warnings"])
    prompt_pack = (
        _load_external_writing_prompt_pack(db, project, warnings)
        if args.get("include_prompt_pack", True)
        else None
    )
    status = "needs_confirmation" if manifest.status == "needs_confirmation" else "ok"
    selection = manifest_payload.get("selection") or {}
    selection_ready = selection.get("status") == "ready" and bool(selection.get("token"))
    safe_task_context = render_generation_context(manifest)
    detail = (
        f"Compact writing anchors prepared: {manifest.estimated_input_tokens}/"
        f"{manifest.input_budget_tokens} available input tokens; "
        f"{TASK_CONTEXT_SOFT_TARGET_TOKENS} is a non-blocking soft target"
    )
    if status == "needs_confirmation":
        detail += ". Required context is missing; author confirmation is required before generation."
    elif not selection_ready:
        detail += ". The Agent must now search and finalize exact evidence before drafting."
    next_tools = [
        {"tool": "search_task_context", "description": "Ask focused model-chosen queries and inspect compact candidates."},
        {"tool": "submit_context_evidence", "description": "Finalize only the exact sources needed for this chapter."},
    ]
    if selection_ready:
        next_tools.append({
            "tool": "save_external_chapter_draft",
            "description": "Use the returned selection token, save one unsaved draft, and end the turn.",
        })
    return {
        "tool": "prepare_external_writing_context",
        "status": status,
        "detail": detail,
        "data": {
            "project": {"id": project.id, "title": project.title},
            "target": {
                "outline_node_id": target_outline.id,
                "title": target_outline.title,
                "draft_kind": "revision" if target_chapter else "new",
                "target_chapter_id": target_chapter.id if target_chapter else None,
                "base_chapter_version": (
                    int(target_chapter.current_version or 1) if target_chapter else None
                ),
            },
            "requirements": str(args.get("requirements") or "").strip(),
            "prompt_pack": prompt_pack,
            "context_manifest_id": manifest.id,
            "context_manifest_status": manifest.status,
            "requires_author_confirmation": status == "needs_confirmation",
            "context_budget": manifest_payload["budget"],
            "context_coverage": manifest_payload["coverage"],
            "baseline_sources": manifest_payload["items"],
            "baseline_context": safe_task_context,
            "selection_required": not selection_ready,
            "context_selection_token": selection.get("token") if selection_ready else None,
            "task_context": safe_task_context if selection_ready else "",
            "warnings": list(dict.fromkeys(warnings)),
            "workflow_boundaries": {
                "current_task": "chapter_revision" if target_chapter else "base_chapter_writing",
                "de_ai_revision": "separate_user_action",
                "quality_review": "separate_user_action",
            },
            "next_tool_suggestions": next_tools,
        },
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
        target_chapter_id = str(args.get("target_chapter_id") or "").strip()
        if not target_chapter_id or target_chapter_id != str(existing_chapter.id):
            return {
                "tool": "prepare_external_writing_context",
                "status": "skipped",
                "detail": (
                    "The selected outline already has formal prose. To prepare a reviewable "
                    "revision, explicitly pass its existing_chapter_id as target_chapter_id."
                ),
                "data": {
                    "outline_node_id": outline_node_id,
                    "existing_chapter_id": existing_chapter.id,
                },
            }
    elif str(args.get("target_chapter_id") or "").strip():
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": "target_chapter_id does not match the formal chapter linked to this outline.",
            "data": {"outline_node_id": outline_node_id},
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

    return _external_writing_context_result(
        db,
        project,
        target_outline,
        existing_chapter,
        manifest,
        manifest_payload,
        args,
    )


def _external_draft_manifest_error(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    context_manifest_id: str | None,
    outline_node_id: str,
) -> dict | None:
    if not context_manifest_id:
        return {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "detail": "Prepare task context first and attach its context_manifest_id to the draft.",
            "data": None,
        }

    from ....services.context_orchestrator import ContextOrchestrator

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(context_manifest_id, project_id)
    if not manifest:
        return {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "detail": "The supplied context manifest is unavailable for this project.",
            "data": {"context_manifest_id": context_manifest_id},
        }
    selection_token = str(args.get("context_selection_token") or "").strip()
    usable, detail = orchestrator.validate_task_selection(
        manifest,
        token=selection_token,
        task_type="writing",
        outline_node_id=outline_node_id,
    )
    if usable:
        if not orchestrator.mark_consumed(manifest):
            detail = "context_selection_token has already been consumed."
        else:
            from app.architecture.uow import commit_session

            commit_session(db)
            return None
    return {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": detail,
        "data": {
            "context_manifest_id": context_manifest_id,
            "outline_node_id": outline_node_id,
        },
    }


def _resolve_external_draft_target(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> tuple[str | None, str, str | None, int | None, dict[str, Any] | None]:
    """Resolve new-vs-revision identity before the terminal save boundary."""
    from app.database.models import Chapter, OutlineNode

    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    if not outline_node_id:
        return None, "", None, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "The Agent must select a real chapter-level outline ID before saving a draft.",
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
        return outline_node_id, "", None, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": "outline_node_id must identify a chapter node in the current project.",
            "data": {"outline_node_id": outline_node_id},
        }
    existing_chapter = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.outline_node_id == outline_node_id,
        )
        .first()
    )
    target_chapter_id = str(args.get("target_chapter_id") or "").strip() or None
    if existing_chapter and target_chapter_id != str(existing_chapter.id):
        return outline_node_id, "", target_chapter_id, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "The selected outline has formal prose. A revision candidate requires "
                "the matching target_chapter_id and never overwrites it automatically."
            ),
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": existing_chapter.id,
            },
        }
    if not existing_chapter and target_chapter_id:
        return outline_node_id, "", target_chapter_id, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "target_chapter_id does not match the formal chapter linked to this outline."
            ),
            "data": {"outline_node_id": outline_node_id},
        }

    requested_base_version = args.get("base_chapter_version")
    if existing_chapter and requested_base_version is None:
        return outline_node_id, "", target_chapter_id, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "A revision must carry the base_chapter_version returned by "
                "prepare_external_writing_context so later author edits cannot be overwritten."
            ),
            "data": {"target_chapter_id": target_chapter_id},
        }
    try:
        base_chapter_version = int(requested_base_version) if existing_chapter else None
    except (TypeError, ValueError):
        return outline_node_id, "", target_chapter_id, None, {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "base_chapter_version must be the integer returned by "
                "prepare_external_writing_context."
            ),
            "data": {"target_chapter_id": target_chapter_id},
        }
    return (
        outline_node_id,
        str(target_outline.title or "").strip(),
        target_chapter_id,
        base_chapter_version,
        None,
    )


async def save_external_chapter_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save an externally generated chapter draft.

    API-free: stores draft content server-side and returns draft_id/content_ref.
    Saving the draft is the terminal boundary for an AI chapter-writing turn.
    """
    from app.services.cataloging.launcher import (
        cataloging_block_result,
        cataloging_required_block_result,
        find_blocking_chapter_cataloging_job,
        find_cataloging_required_chapter,
    )
    from app.services.workspace.generated_drafts import (
        ChapterDraftOutlineConflict,
        ChapterDraftTargetConflict,
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

    context_manifest_id = str(args.get("context_manifest_id") or "").strip() or None
    source_agent = str(args.get("source_agent") or "external").strip()
    (
        outline_node_id,
        title,
        target_chapter_id,
        base_chapter_version,
        target_error,
    ) = _resolve_external_draft_target(db, project_id, args)
    if target_error:
        return target_error
    assert outline_node_id is not None

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

    manifest_error = _external_draft_manifest_error(
        db,
        project_id,
        args,
        context_manifest_id,
        outline_node_id,
    )
    if manifest_error:
        return manifest_error

    try:
        draft_id = store_chapter_draft(
            project_id=project_id,
            content=content,
            title=title,
            outline_node_id=outline_node_id,
            context_manifest_id=context_manifest_id,
            target_chapter_id=target_chapter_id or None,
            base_chapter_version=base_chapter_version,
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
                "existing_chapter_id": getattr(
                    conflict.chapter,
                    "id",
                    target_chapter_id or None,
                ),
            },
        }
    except ChapterDraftTargetConflict as conflict:
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": (
                "The revision target changed before the candidate was stored; "
                "no prose was overwritten."
            ),
            "data": {"target_chapter_id": conflict.target_chapter_id},
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
            "draft_kind": "revision" if target_chapter_id else "new",
            "target_chapter_id": target_chapter_id or None,
            "base_chapter_version": base_chapter_version,
            "next_actions": ["save_and_catalog", "save_only"],
            "word_count": count_words(content),
            "source_agent": source_agent,
        },
    }, AssistantTurnDirective.END_AFTER_DRAFT)


async def save_external_outline_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Persist an external Agent's reviewed-context outline proposal."""
    from ....services.context_orchestrator import ContextOrchestrator
    from ....services.story_granularity import normalize_outline_batch
    from ..outline_drafts import (
        PendingOutlineDraftConflict,
        latest_pending_outline_draft,
        outline_draft_result_data,
        pending_outline_draft_block_result,
        store_outline_draft,
    )
    from ..turn_control import AssistantTurnDirective, apply_turn_directive

    manifest_id = str(args.get("context_manifest_id") or "").strip()
    token = str(args.get("context_selection_token") or "").strip()
    parent_id = str(args.get("parent_id") or "").strip() or None
    insert_after_id = str(args.get("insert_after_id") or "").strip() or None
    pending = latest_pending_outline_draft(db, project_id)
    if pending:
        return pending_outline_draft_block_result(
            "save_external_outline_draft",
            pending,
        )
    manifest = ContextOrchestrator(db).get_manifest(manifest_id, project_id)
    if manifest is None:
        return {
            "tool": "save_external_outline_draft",
            "status": "needs_confirmation",
            "detail": "Prepare outline_planning task context first.",
            "data": {"context_manifest_id": manifest_id or None},
        }
    orchestrator = ContextOrchestrator(db)
    usable, detail = orchestrator.validate_task_selection(
        manifest,
        token=token,
        task_type="outline_planning",
        parent_id=parent_id,
        insert_after_id=insert_after_id,
    )
    if not usable:
        return {
            "tool": "save_external_outline_draft",
            "status": "needs_confirmation",
            "detail": detail,
            "data": {"context_manifest_id": manifest.id},
        }
    if not orchestrator.mark_consumed(manifest):
        return {
            "tool": "save_external_outline_draft",
            "status": "needs_confirmation",
            "detail": "context_selection_token has already been consumed.",
            "data": {"context_manifest_id": manifest.id},
        }
    from app.architecture.uow import commit_session

    commit_session(db)
    raw_nodes = args.get("nodes") if isinstance(args.get("nodes"), list) else []
    if len(raw_nodes) > 8 or any(not isinstance(node, dict) for node in raw_nodes):
        return {
            "tool": "save_external_outline_draft",
            "status": "error",
            "detail": "Outline proposal must contain one to eight valid node objects.",
            "data": {},
        }
    nodes = normalize_outline_batch(
        [dict(node) for node in raw_nodes]
    )
    if not nodes:
        return {
            "tool": "save_external_outline_draft",
            "status": "skipped",
            "detail": "Outline proposal contains no valid nodes.",
            "data": {},
        }
    for node in nodes:
        node["status"] = "pending"
    try:
        draft = store_outline_draft(
            db,
            project_id=project_id,
            context_manifest_id=manifest.id,
            parent_id=parent_id,
            insert_after_id=insert_after_id,
            nodes=nodes,
            design_notes=str(args.get("design_notes") or ""),
            context_selection_token=token,
        )
    except PendingOutlineDraftConflict as conflict:
        return pending_outline_draft_block_result(
            "save_external_outline_draft",
            conflict.draft,
        )
    return apply_turn_directive(
        {
            "tool": "save_external_outline_draft",
            "status": "ok",
            "detail": "大纲草稿已保存，等待作者确认",
            "data": outline_draft_result_data(draft),
        },
        AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
    )


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
