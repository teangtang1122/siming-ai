"""External writing tools — API-free tools for external agents (Claude Code, Codex).

These tools work without any Siming model API configured. They provide
context, focused writing prompts, and draft storage for external agents that
do their own generation. Quality review remains available as a separate tool.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....core.utils import count_words
from ....services.chapter_writing_constraints import (
    check_chapter_length,
    manifest_minimum_han_characters,
    normalize_writing_arguments,
    recommended_han_character_target,
)
from ....services.task_context_delivery import (
    build_context_page,
    context_delivery_ready,
    context_delivery_state,
    context_delivery_status,
    context_page_arguments,
    deliver_next_context_page,
)
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
            PublicPromptPack.enabled,
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
    source_draft: Any | None,
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
    # Before evidence selection this endpoint also carries the writing prompt
    # pack.  Once evidence is selected, however, every model-visible entrypoint
    # must page the same canonical generation document used by
    # submit_context_evidence.  Otherwise an Agent could obtain a token from
    # this legacy wrapper without reading the selected source pages.
    document = (
        safe_task_context
        if selection_ready
        else "\n\n".join(
            part
            for part in (
                str((prompt_pack or {}).get("system_prompt") or ""),
                safe_task_context,
            )
            if part
        )
    )
    selection_token = str(selection.get("token") or "")
    delivery_state = context_delivery_state(manifest)
    try:
        if selection_ready:
            page, delivery_state = deliver_next_context_page(
                manifest,
                document,
                args,
                selection_token,
            )
        else:
            page = build_context_page(document, args)
    except ValueError as error:
        return {"tool": "prepare_external_writing_context", "status": "skipped", "detail": str(error),
                "data": {"context_manifest_id": manifest.id}}
    db.flush()
    delivery_ready = selection_ready and context_delivery_ready(manifest, selection_token)
    page_arguments = context_page_arguments(manifest.id, "writing", page)
    if not selection_ready:
        # Pre-selection pages include the prompt pack and therefore remain on
        # this endpoint. Selected evidence pages use prepare_task_context's
        # canonical document and argument contract.
        page_arguments.update({
            "outline_node_id": target_outline.id,
            "include_prompt_pack": args.get("include_prompt_pack", True),
        })
        if target_chapter:
            page_arguments["target_chapter_id"] = target_chapter.id
        if source_draft:
            page_arguments["source_draft_id"] = source_draft.id
    detail = (
        f"Compact writing anchors prepared: {manifest.estimated_input_tokens}/"
        f"{manifest.input_budget_tokens} available input tokens; "
        f"{TASK_CONTEXT_SOFT_TARGET_TOKENS} is a non-blocking soft target"
    )
    if status == "needs_confirmation":
        detail += (
            ". Required context is missing; author confirmation is required "
            "before generation."
        )
    elif not selection_ready:
        detail += ". The Agent must now search and finalize exact evidence before drafting."
    elif not delivery_ready:
        detail += (
            ". Exact evidence was selected, but its remaining pages must be read in order; "
            "the selection token is withheld until the final page."
        )
    next_tools = [
        {
            "tool": "search_task_context",
            "description": "Ask focused model-chosen queries and inspect compact candidates.",
        },
        {
            "tool": "submit_context_evidence",
            "description": "Finalize only the exact sources needed for this chapter.",
        },
    ]
    if page["has_more"]:
        next_tools = [{"tool": (
                           "prepare_task_context" if selection_ready
                           else "prepare_external_writing_context"
                       ), "arguments": page_arguments,
                       "description": "Read the remaining context pages; source text has not been truncated."}]
    if delivery_ready:
        next_tools.append({
            "tool": "save_external_chapter_draft",
            "description": (
                "Use the returned selection token, save one unsaved draft, and end the turn."
            ),
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
                "source_draft_id": source_draft.id if source_draft else None,
            },
            "prompt_pack": {key: value for key, value in prompt_pack.items() if key != "system_prompt"} if prompt_pack else None,
            "context_manifest_id": manifest.id,
            "context_manifest_status": manifest.status,
            "requires_author_confirmation": status == "needs_confirmation",
            "context_budget": manifest_payload["budget"],
            "context_coverage": manifest_payload["coverage"],
            "context_page": page,
            "selection_required": not selection_ready,
            "context_selection_token": selection_token if delivery_ready else None,
            "context_delivery_ready": delivery_ready,
            "context_delivery": context_delivery_status(delivery_state),
            "writing_constraints": {
                "minimum_han_characters": manifest_minimum_han_characters(manifest),
                "metric": "cjk_unified_ideographs",
                "enforced_before_draft_storage": True,
            },
            "warnings": list(dict.fromkeys(warnings)),
            "workflow_boundaries": {
                "current_task": (
                    "pending_draft_revision"
                    if source_draft
                    else ("chapter_revision" if target_chapter else "base_chapter_writing")
                ),
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
    from app.services.workspace.generated_drafts import (
        find_chapter_draft,
        find_pending_chapter_draft,
    )

    try:
        args = normalize_writing_arguments(args)
    except ValueError as error:
        return {
            "tool": "prepare_external_writing_context",
            "status": "skipped",
            "detail": str(error),
            "data": None,
        }

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
            "detail": (
                "The Agent must select a real chapter-level outline ID before "
                "preparing writing context."
            ),
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
    source_draft_id = str(args.get("source_draft_id") or "").strip() or None
    source_draft = None
    if source_draft_id:
        pending = find_pending_chapter_draft(db, project_id)
        source_draft = find_chapter_draft(db, project_id, source_draft_id)
        if (
            source_draft is None
            or str(source_draft.status or "") != "pending"
            or pending is None
            or str(pending.id) != source_draft_id
            or str(source_draft.outline_node_id or "") != outline_node_id
        ):
            return {
                "tool": "prepare_external_writing_context",
                "status": "skipped",
                "detail": "source_draft_id must identify the current pending draft for this outline.",
                "data": {"source_draft_id": source_draft_id},
            }
        draft_target_id = str(source_draft.target_chapter_id or "").strip()
        requested_target_id = str(args.get("target_chapter_id") or "").strip()
        if requested_target_id and requested_target_id != draft_target_id:
            return {
                "tool": "prepare_external_writing_context",
                "status": "skipped",
                "detail": "target_chapter_id does not match the current pending draft.",
                "data": {"source_draft_id": source_draft_id},
            }
        args["target_chapter_id"] = draft_target_id or None

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
            "detail": "Context index rebuild is incomplete; check the project context status before retrying.",
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
        source_draft,
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
    source_draft_id: str | None,
) -> dict | None:
    if not context_manifest_id:
        return {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "detail": "Prepare task context first and attach its context_manifest_id to the draft.",
            "data": {"reason_code": "context_manifest_required"},
        }

    from ....services.context_orchestrator import ContextOrchestrator

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(context_manifest_id, project_id)
    if not manifest:
        return {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "detail": "The supplied context manifest is unavailable for this project.",
            "data": {
                "reason_code": "context_manifest_unavailable",
                "context_manifest_id": context_manifest_id,
            },
        }
    selection_token = str(args.get("context_selection_token") or "").strip()
    usable, detail = orchestrator.validate_task_selection(
        manifest,
        token=selection_token,
        task_type="writing",
        outline_node_id=outline_node_id,
        source_draft_id=source_draft_id,
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
            "reason_code": "context_selection_invalid",
            "context_manifest_id": context_manifest_id,
            "outline_node_id": outline_node_id,
        },
    }


def _external_draft_length_error(
    db: Session,
    project_id: str,
    context_manifest_id: str | None,
    outline_node_id: str,
    content: str,
) -> dict[str, Any] | None:
    """Reject a short draft before consuming its one-use evidence token."""
    if not context_manifest_id:
        return None

    from ....services.context_orchestrator import ContextOrchestrator

    manifest = ContextOrchestrator(db).get_manifest(context_manifest_id, project_id)
    if not manifest:
        return None
    try:
        check = check_chapter_length(content, manifest)
    except ValueError as error:
        return {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "detail": f"The prepared writing constraint is invalid: {error}",
            "data": {
                "reason_code": "writing_constraint_invalid",
                "context_manifest_id": context_manifest_id,
            },
        }
    if check.accepted:
        return None
    minimum = int(check.minimum_han_characters or 0)
    missing = minimum - check.actual_han_characters
    recommended = recommended_han_character_target(minimum)
    recommended_additional = recommended - check.actual_han_characters
    return {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": (
            f"正文只有 {check.actual_han_characters} 个汉字，低于已绑定的硬下限 "
            f"{minimum}；未保存草稿，也未消耗上下文令牌。至少还差 {missing} 个；"
            f"为减少反复退回，建议一次补至 {recommended} 个汉字（约再补 "
            f"{recommended_additional} 个），再用同一清单和令牌重试。"
        ),
        "data": {
            "reason_code": "draft_below_minimum",
            "context_manifest_id": context_manifest_id,
            "outline_node_id": outline_node_id,
            "actual_han_characters": check.actual_han_characters,
            "minimum_han_characters": minimum,
            "missing_han_characters": missing,
            "recommended_han_characters": recommended,
            "recommended_additional_han_characters": recommended_additional,
            "draft_stored": False,
            "context_selection_token_consumed": False,
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
    """Persist an external draft; this is the terminal AI-writing boundary."""
    from app.services.cataloging.launcher import (
        cataloging_block_result,
        cataloging_required_block_result,
        find_blocking_chapter_cataloging_job,
        find_cataloging_required_chapter,
    )
    from app.services.context_orchestrator import ContextOrchestrator
    from app.services.workspace.generated_drafts import (
        ChapterDraftOutlineConflict,
        ChapterDraftRevisionConflict,
        ChapterDraftTargetConflict,
        PendingChapterDraftConflict,
        chapter_draft_result_data,
        find_chapter_draft,
        find_pending_chapter_draft,
        pending_draft_block_result,
        replace_pending_chapter_draft_after_ai_revision,
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
    source_draft_id = str(args.get("source_draft_id") or "").strip() or None
    source_draft = None
    if source_draft_id:
        pending = find_pending_chapter_draft(db, project_id)
        source_draft = find_chapter_draft(db, project_id, source_draft_id)
        requested_outline_id = str(args.get("outline_node_id") or "").strip()
        if (
            source_draft is None
            or str(source_draft.status or "") != "pending"
            or pending is None
            or str(pending.id) != source_draft_id
            or str(source_draft.outline_node_id or "") != requested_outline_id
        ):
            return {
                "tool": "save_external_chapter_draft",
                "status": "skipped",
                "detail": "source_draft_id must identify the current pending draft for this outline.",
                "data": {"source_draft_id": source_draft_id},
            }
        args = dict(args)
        draft_target_id = str(source_draft.target_chapter_id or "").strip() or None
        supplied_target_id = str(args.get("target_chapter_id") or "").strip() or None
        if supplied_target_id and supplied_target_id != draft_target_id:
            return {
                "tool": "save_external_chapter_draft",
                "status": "skipped",
                "detail": "target_chapter_id does not match the current pending draft.",
                "data": {"source_draft_id": source_draft_id},
            }
        args["target_chapter_id"] = draft_target_id
        if draft_target_id:
            supplied_base = args.get("base_chapter_version")
            try:
                supplied_base_version = (
                    int(supplied_base) if supplied_base is not None else None
                )
            except (TypeError, ValueError):
                return {
                    "tool": "save_external_chapter_draft",
                    "status": "skipped",
                    "detail": (
                        "base_chapter_version must be the integer returned by "
                        "prepare_external_writing_context."
                    ),
                    "data": {"source_draft_id": source_draft_id},
                }
            if supplied_base_version is not None and supplied_base_version != int(
                source_draft.base_chapter_version or 0
            ):
                return {
                    "tool": "save_external_chapter_draft",
                    "status": "skipped",
                    "detail": "base_chapter_version does not match the current pending draft.",
                    "data": {"source_draft_id": source_draft_id},
                }
            args["base_chapter_version"] = source_draft.base_chapter_version
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
    if pending_draft and source_draft is None:
        return pending_draft_block_result("save_external_chapter_draft", pending_draft)
    if source_draft is None and target_chapter_id is None:
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

    length_error = _external_draft_length_error(
        db,
        project_id,
        context_manifest_id,
        outline_node_id,
        content,
    )
    if length_error:
        return length_error

    manifest_error = _external_draft_manifest_error(
        db,
        project_id,
        args,
        context_manifest_id,
        outline_node_id,
        source_draft_id,
    )
    if manifest_error:
        return manifest_error

    try:
        if source_draft_id:
            manifest = ContextOrchestrator(db).get_manifest(
                context_manifest_id, project_id
            ) if context_manifest_id else None
            source_item = next(
                (
                    item
                    for item in ContextOrchestrator(db).task_generation_items(manifest)
                    if item.category == "target_draft"
                    and str(item.source_id or "") == source_draft_id
                ),
                None,
            ) if manifest is not None else None
            source_draft = replace_pending_chapter_draft_after_ai_revision(
                db,
                project_id,
                source_draft_id,
                expected_source_hash=str(getattr(source_item, "source_hash", "") or ""),
                content=content,
                context_manifest_id=str(context_manifest_id or ""),
            )
            draft_id = source_draft_id
        else:
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
    except ChapterDraftRevisionConflict as conflict:
        data = (
            chapter_draft_result_data(conflict.draft, db=db)
            if conflict.draft is not None
            else {"draft_id": source_draft_id}
        )
        data["late_result_discarded"] = True
        return {
            "tool": "save_external_chapter_draft",
            "status": "skipped",
            "detail": f"{conflict.reason}; the late external revision did not overwrite the draft.",
            "data": data,
        }
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

    manifest = ContextOrchestrator(db).get_manifest(
        context_manifest_id, project_id
    ) if context_manifest_id else None
    length_check = check_chapter_length(content, manifest)
    return apply_turn_directive({
        "tool": "save_external_chapter_draft",
        "status": "ok",
        "detail": (
            f"当前章节草稿已修改（{count_words(content)} 字），仍未保存；本轮必须结束"
            if source_draft_id
            else f"章节草稿已生成（{count_words(content)} 字），尚未保存；本轮必须结束"
        ),
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "title": str(source_draft.title or "") if source_draft is not None else title,
            "outline_node_id": outline_node_id,
            "context_manifest_id": context_manifest_id,
            "content": content,
            "draft_status": "pending",
            "draft_kind": (
                str(source_draft.draft_kind or "new")
                if source_draft is not None
                else ("revision" if target_chapter_id else "new")
            ),
            "target_chapter_id": (
                source_draft.target_chapter_id if source_draft is not None else target_chapter_id
            ) or None,
            "base_chapter_version": (
                source_draft.base_chapter_version
                if source_draft is not None
                else base_chapter_version
            ),
            "source_draft_id": source_draft_id,
            "next_actions": ["revise_draft", "save_and_catalog", "save_only", "discard"],
            "word_count": count_words(content),
            "han_character_count": length_check.actual_han_characters,
            "minimum_han_characters": length_check.minimum_han_characters,
            "source_agent": source_agent,
        },
    }, AssistantTurnDirective.END_AFTER_DRAFT)


async def save_external_outline_draft(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Persist an external Agent's reviewed-context outline proposal."""
    from ....core.exceptions import ValidationError
    from ....services.context_orchestrator import ContextOrchestrator
    from ..outline_drafts import (
        PendingOutlineDraftConflict,
        latest_pending_outline_draft,
        outline_draft_result_data,
        pending_outline_draft_block_result,
        store_outline_draft,
        validate_generated_outline_proposal,
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
    try:
        _, nodes = validate_generated_outline_proposal(
            db, project_id=project_id, manifest=manifest, parent_id=parent_id,
            insert_after_id=insert_after_id, nodes=args.get("nodes"),
        )
    except ValidationError as exc:
        return {
            "tool": "save_external_outline_draft", "status": "error",
            "detail": str(exc), "data": {"context_manifest_id": manifest.id},
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
    except ValidationError as exc:
        return {
            "tool": "save_external_outline_draft", "status": "error",
            "detail": str(exc), "data": {"context_manifest_id": manifest.id},
        }
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
    from app.services.workspace.generated_drafts import (
        chapter_draft_result_data,
        get_chapter_draft,
        latest_pending_chapter_draft,
    )

    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip()
    if not draft_id:
        from app.database.models import ChapterDraft

        pending = latest_pending_chapter_draft(db, project_id)
        if isinstance(pending, ChapterDraft):
            return {
                "tool": "get_external_chapter_draft",
                "status": "ok",
                "detail": "Current pending chapter draft retrieved.",
                "data": chapter_draft_result_data(pending, db=db),
            }
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": "No pending chapter draft exists in this project.",
            "data": None,
        }

    draft_content = get_chapter_draft(project_id, draft_id, db=db)
    if not draft_content:
        return {
            "tool": "get_external_chapter_draft",
            "status": "skipped",
            "detail": f"Draft not found: {draft_id}",
            "data": None,
        }

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
                    "character_consistency": (
                        "character_consistency",
                        "character",
                        "角色一致性",
                        "角色塑造",
                    ),
                    "viewpoint_consistency": ("viewpoint_consistency", "viewpoint", "视角一致性"),
                    "world_consistency": (
                        "world_consistency",
                        "world",
                        "设定一致性",
                        "世界观一致性",
                    ),
                }
                metric = {
                    "chapter_id": chapter_id,
                    "passed": bool(passed),
                    "warnings": list(issues or []),
                    "evidence": "；".join(str(item) for item in suggestions[:10]),
                    "source": "external_agent",
                }
                for target, names in aliases.items():
                    value = next(
                        (
                            scores[name]
                            for name in names
                            if isinstance(scores.get(name), (int, float))
                        ),
                        None,
                    )
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
                review["draft_content_length"] = (
                    len(draft_content) if isinstance(draft_content, str) else 0
                )
        except Exception:
            pass  # Draft lookup is optional

    return {
        "tool": "record_external_quality_review",
        "status": "ok",
        "detail": f"Review recorded: {'PASS' if passed else 'FAIL'}"
        + (f" (total: {review.get('total_score', '?')})" if scores else ""),
        "data": review,
    }
