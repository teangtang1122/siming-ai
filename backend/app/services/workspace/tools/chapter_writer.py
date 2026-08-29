"""Chapter Writer workspace tool — generates chapter body prose with full writing rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....ai.local_cli_adapter import is_local_cli_provider
from ....core.utils import count_words
from ....database.models import (
    Chapter,
    OutlineNode,
    Project,
)
from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ....services.agent.prompt_builder import compose_chapter_writer_messages, get_chapter_pack
from ....services.cataloging.launcher import (
    cataloging_block_result,
    cataloging_required_block_result,
    find_blocking_chapter_cataloging_job,
    find_cataloging_required_chapter,
)
from ....services.context_orchestrator import ContextOrchestrator
from ..generated_drafts import (
    ChapterDraftOutlineConflict,
    PendingChapterDraftConflict,
    find_pending_chapter_draft,
    pending_draft_block_result,
    store_chapter_draft,
)
from ..turn_control import AssistantTurnDirective, apply_turn_directive


def _chapter_writer_provider(model: str | None) -> str:
    try:
        provider, _ = LLMGateway.model_identity(model, {"moshu_task_type": "writing"})
        return provider
    except Exception:
        return (model or "").split(":", 1)[0].strip().lower()


def _chapter_writer_limits(model: str | None) -> tuple[int, int]:
    provider = _chapter_writer_provider(model)
    if is_local_cli_provider(provider):
        return 360, 7000
    return 300, 7000


def _writer_result(status: str, detail: str, data: dict | None = None) -> dict:
    return {
        "tool": "chapter_writer",
        "status": status,
        "detail": detail,
        "data": data or {},
    }


def _chapter_writer_target(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
) -> tuple[Any | None, dict | None]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None, _writer_result("skipped", "项目不存在")
    if not outline_node_id:
        return None, _writer_result(
            "skipped",
            "必须先根据用户当前消息确定章级大纲节点，再生成正文",
        )
    target_outline = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id == outline_node_id)
        .first()
    )
    if not target_outline or target_outline.node_type != "chapter":
        return None, _writer_result(
            "skipped",
            "outline_node_id 必须是当前作品的章级节点，不能使用卷级或场景级节点",
            {"outline_node_id": outline_node_id},
        )
    existing_chapter = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.outline_node_id == outline_node_id,
        )
        .first()
    )
    if existing_chapter:
        return None, _writer_result(
            "skipped",
            "该章级大纲已关联正式章节；章节写作只能生成新章草稿，不能覆盖已有正文",
            {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": existing_chapter.id,
            },
        )
    pending_draft = find_pending_chapter_draft(db, project_id)
    if pending_draft:
        return None, pending_draft_block_result("chapter_writer", pending_draft)
    blocking_job = find_blocking_chapter_cataloging_job(db, project_id)
    if blocking_job:
        return None, cataloging_block_result("chapter_writer", blocking_job)
    required_chapter = find_cataloging_required_chapter(db, project_id)
    if required_chapter:
        return None, cataloging_required_block_result("chapter_writer", required_chapter)
    return target_outline, None


def _prepare_chapter_writer_manifest(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    outline_node_id: str,
) -> tuple[ContextOrchestrator, Any | None, dict | None]:
    # Writing never creates context implicitly. The outer Agent must first
    # inspect the compact anchors, search focused gaps, and finalize exact
    # evidence in a previous model step.
    orchestrator = ContextOrchestrator(db)
    requested_manifest_id = str(args.get("context_manifest_id") or "").strip()
    selection_token = str(args.get("context_selection_token") or "").strip()
    if not requested_manifest_id:
        return orchestrator, None, _writer_result(
            "needs_confirmation",
            "必须先建立写章上下文基线，并让模型检索、复核所需资料。",
            {"next_tool": "prepare_task_context"},
        )
    manifest = orchestrator.get_manifest(requested_manifest_id, project_id)
    if not manifest:
        return orchestrator, None, _writer_result(
            "needs_confirmation",
            "The requested context manifest was not found.",
            {"context_manifest_id": requested_manifest_id},
        )
    manifest_ok, manifest_detail = orchestrator.validate_task_selection(
        manifest,
        token=selection_token,
        task_type="writing",
        outline_node_id=outline_node_id,
    )
    if not manifest_ok:
        status = (
            manifest.status
            if manifest.status in {"needs_confirmation", "blocked_rebuild", "stale"}
            else "needs_confirmation"
        )
        return orchestrator, None, _writer_result(
            status,
            manifest_detail,
            {
                "context_manifest_id": manifest.id,
                "context_manifest": orchestrator.manifest_payload(
                    manifest,
                    include_content=False,
                ),
            },
        )
    return orchestrator, manifest, None


@dataclass(slots=True)
class _GeneratedChapterProse:
    content: str
    model_result: dict[str, Any]
    context_snapshot: dict[str, Any]


async def _generate_chapter_prose(
    db: Session,
    project_id: str,
    outline_node_id: str,
    model: str | None,
    orchestrator: ContextOrchestrator,
    manifest: Any,
) -> tuple[_GeneratedChapterProse | None, dict | None]:
    generation_items = orchestrator.task_generation_items(manifest)
    outline_ctx = (
        _manifest_item_text(
            generation_items,
            categories={"target_outline"},
        )
        or "No target outline was selected."
    )
    supporting_outlines = _manifest_item_text(
        generation_items,
        categories={"agent_selected", "pinned"},
        source_types={"outline"},
    )
    if supporting_outlines:
        outline_ctx = f"{outline_ctx}\n\n{supporting_outlines}"
    summaries = (
        _manifest_item_text(
            generation_items,
            categories={"agent_selected", "pinned"},
            source_types={"chapter", "chapter_summary"},
        )
        or "No previous chapter summary is available."
    )
    characters = (
        _manifest_item_text(
            generation_items,
            categories={"agent_selected", "pinned"},
            source_types={"character", "character_timeline"},
        )
        or "No scene character was selected."
    )
    world_ctx = _manifest_item_text(
        generation_items,
        categories={"agent_selected", "pinned"},
        excluded_source_types={
            "outline", "chapter", "chapter_summary", "character", "character_timeline",
        },
    ) or "No additional worldbuilding source was selected."
    style_ctx = _manifest_item_text(
        generation_items,
        categories={"style"},
    ) or "Use the project's established style."
    requirements = _manifest_item_text(
        generation_items,
        categories={"user_requirement"},
    )
    messages = compose_chapter_writer_messages(
        pack=get_chapter_pack(),
        style_context=style_ctx,
        outline_context=outline_ctx,
        world_context=world_ctx,
        character_profiles=characters,
        recent_summaries=summaries,
        requirements=requirements,
    )
    timeout_seconds, max_output_tokens = _chapter_writer_limits(model)
    max_output_tokens = min(max_output_tokens, max(1, manifest.output_reserve_tokens))
    gateway_extra = LLMGateway.local_cli_extra_body(
        model,
        base={
            "moshu_task_type": "writing",
            "moshu_project_id": project_id,
            "moshu_context_manifest_id": manifest.id,
            "moshu_context_manifest_rendered": True,
            "local_cli_isolated": True,
        },
    )
    commit_session(db)
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.8,
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            retry=1,
            extra_body=gateway_extra,
        )
    except Exception as exc:
        return None, _writer_result("error", f"章节正文生成失败: {exc}")
    content = (result.get("content") or "").strip()
    if not content:
        return None, _writer_result("error", "生成的章节正文为空")
    return _GeneratedChapterProse(
        content=content,
        model_result=result,
        context_snapshot=_manifest_snapshot(
            orchestrator,
            manifest,
            outline_node_id,
        ),
    ), None


async def chapter_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Generate an independent chapter draft for the model-selected outline."""
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    target_outline, preflight_error = _chapter_writer_target(
        db,
        project_id,
        outline_node_id,
    )
    if preflight_error:
        return preflight_error

    model = str(args.get("model") or "") or None
    orchestrator, manifest, manifest_error = _prepare_chapter_writer_manifest(
        db,
        project_id,
        args,
        str(outline_node_id),
    )
    if manifest_error:
        return manifest_error
    if not orchestrator.mark_consumed(manifest):
        return _writer_result(
            "needs_confirmation",
            "context_selection_token 已使用；请重新检索并提交资料。",
            {"context_manifest_id": manifest.id},
        )
    commit_session(db)
    generated, generation_error = await _generate_chapter_prose(
        db,
        project_id,
        str(outline_node_id),
        model,
        orchestrator,
        manifest,
    )
    if generation_error:
        return generation_error

    content = generated.content
    outline_title = target_outline.title or ""
    manifest_id = manifest.id

    try:
        draft_id = store_chapter_draft(
            project_id=project_id,
            content=content,
            title=outline_title,
            outline_node_id=outline_node_id,
            context_manifest_id=manifest_id,
            db=db,
        )
    except PendingChapterDraftConflict as conflict:
        return pending_draft_block_result("chapter_writer", conflict.draft)
    except ChapterDraftOutlineConflict as conflict:
        return _writer_result(
            "skipped",
            (
                "生成期间该大纲已保存为正式章节；"
                "迟到结果未覆盖正文，也未创建无效草稿"
            ),
            {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": conflict.chapter.id,
            },
        )

    return apply_turn_directive({
        "tool": "chapter_writer",
        "status": "ok",
        "detail": f"已生成章节草稿（{count_words(content)} 字），尚未保存",
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "content": content,
            "title": outline_title,
            "outline_node_id": outline_node_id,
            "draft_status": "pending",
            "next_actions": ["save_and_catalog", "save_only"],
            "word_count": count_words(content),
            "model": generated.model_result.get("model", ""),
            "context_manifest_id": manifest_id,
            "context_snapshot": generated.context_snapshot,
        },
    }, AssistantTurnDirective.END_AFTER_DRAFT)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _manifest_item_text(
    items: list[Any],
    *,
    categories: set[str],
    source_types: set[str] | None = None,
    excluded_source_types: set[str] | None = None,
) -> str:
    """Render only the Agent-finalized evidence requested by one prompt section."""
    source_types = source_types or set()
    excluded_source_types = excluded_source_types or set()
    return "\n\n".join(
        item.content_excerpt
        for item in items
        if item.category in categories
        and (not source_types or item.source_type in source_types)
        and item.source_type not in excluded_source_types
        and item.content_excerpt
    ).strip()


def _manifest_snapshot(
    orchestrator: ContextOrchestrator,
    context_manifest,
    outline_node_id: str | None,
) -> dict:
    """Compact, content-free UI snapshot generated from the manifest."""
    payload = orchestrator.manifest_payload(context_manifest, include_content=False)
    generation_items = orchestrator.task_generation_items(context_manifest)
    selected_items = [
        item
        for item in generation_items
        if item.category == "agent_selected" or item.pinned
    ]
    return {
        "manifest_id": context_manifest.id,
        "status": context_manifest.status,
        "outline_node_id": outline_node_id,
        "involved_characters": [
            item.title
            for item in selected_items
            if item.source_type in {"character", "character_timeline"}
        ],
        "rag_used": bool(selected_items),
        "total_used_chars": context_manifest.estimated_input_chars,
        "total_estimated_tokens": context_manifest.estimated_input_tokens,
        "input_budget_tokens": context_manifest.input_budget_tokens,
        "output_reserve_tokens": context_manifest.output_reserve_tokens,
        "context_window_tokens": context_manifest.context_window_tokens,
        "coverage": context_manifest.coverage_json or {},
        "sections": [
            {
                "category": item["category"],
                "title": item["title"],
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "selection_reason": item["selection_reason"],
                "used_chars": 0,
                "estimated_tokens": item["estimated_tokens"],
                "score": item["scores"]["final"] or 0,
                "chunk_count": 1 if item["chunk_id"] else 0,
                "required": item["required"],
                "pinned": item["pinned"],
            }
            for item in payload["items"]
            if item["category"] not in {"agent_search"}
        ],
        "warnings": payload["warnings"],
        "explanations": [
            item["selection_reason"]
            for item in payload["items"]
            if item["category"] != "agent_search"
        ],
    }
