"""Chapter Writer workspace tool — generates chapter body prose with full writing rules."""
from __future__ import annotations

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
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"tool": "chapter_writer", "status": "skipped", "detail": "项目不存在", "data": {}}

    if not outline_node_id:
        return {
            "tool": "chapter_writer",
            "status": "skipped",
            "detail": "必须先根据用户当前消息确定章级大纲节点，再生成正文",
            "data": {},
        }
    target_outline = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id == outline_node_id)
        .first()
    )
    if not target_outline or target_outline.node_type != "chapter":
        return {
            "tool": "chapter_writer",
            "status": "skipped",
            "detail": "outline_node_id 必须是当前作品的章级节点，不能使用卷级或场景级节点",
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
    if existing_chapter:
        return {
            "tool": "chapter_writer",
            "status": "skipped",
            "detail": "该章级大纲已关联正式章节；章节写作只能生成新章草稿，不能覆盖已有正文",
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": existing_chapter.id,
            },
        }

    pending_draft = find_pending_chapter_draft(db, project_id)
    if pending_draft:
        return pending_draft_block_result("chapter_writer", pending_draft)
    blocking_job = find_blocking_chapter_cataloging_job(
        db,
        project_id,
    )
    if blocking_job:
        return cataloging_block_result("chapter_writer", blocking_job)
    required_chapter = find_cataloging_required_chapter(
        db,
        project_id,
    )
    if required_chapter:
        return cataloging_required_block_result("chapter_writer", required_chapter)

    # Every model path starts with the same persisted manifest. Existing tool
    # callers do not need to provide an id; missing anchors become a
    # recoverable confirmation state instead of a blind model call.
    model = str(args.get("model") or "") or None
    context_orchestrator = ContextOrchestrator(db)
    requested_manifest_id = str(args.get("context_manifest_id") or "").strip()
    if requested_manifest_id:
        context_manifest = context_orchestrator.get_manifest(requested_manifest_id, project_id)
        if not context_manifest:
            return {
                "tool": "chapter_writer",
                "status": "needs_confirmation",
                "detail": "The requested context manifest was not found.",
                "data": {"context_manifest_id": requested_manifest_id},
            }
    else:
        context_manifest = context_orchestrator.prepare(
            project_id=project_id,
            task_type="writing",
            model=model,
            execution_route="internal_cli" if is_local_cli_provider(_chapter_writer_provider(model)) else "internal_api",
            arguments={**args, "involved_characters": involved_names},
            pinned_chunk_ids=args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else (),
        )
    manifest_ok, manifest_detail = context_orchestrator.validate(context_manifest)
    if not manifest_ok:
        return {
            "tool": "chapter_writer",
            "status": context_manifest.status if context_manifest.status in {"needs_confirmation", "blocked_rebuild", "stale"} else "needs_confirmation",
            "detail": manifest_detail,
            "data": {
                "context_manifest_id": context_manifest.id,
                "context_manifest": context_orchestrator.manifest_payload(context_manifest, include_content=False),
            },
        }

    outline_ctx = _manifest_section_text(context_manifest, "target_outline") or "No target outline was selected."
    summaries = _manifest_section_text(context_manifest, "previous_summary") or "No previous chapter summary is available."
    char_detail_text = _manifest_section_text(context_manifest, "scene_character") or "No scene character was selected."
    world_ctx = _manifest_section_text(
        context_manifest,
        "worldbuilding",
        "hybrid_retrieval",
        "narrative_governance",
        "pinned",
    ) or "No additional worldbuilding source was selected."
    style_ctx = _manifest_section_text(context_manifest, "style") or "Use the project's established style."
    context_snapshot = _manifest_snapshot(context_orchestrator, context_manifest, outline_node_id, involved_names)

    outline_title = target_outline.title or ""

    pack = get_chapter_pack()
    messages = compose_chapter_writer_messages(
        pack=pack,
        style_context=style_ctx,
        outline_context=outline_ctx,
        world_context=world_ctx,
        character_profiles=char_detail_text,
        recent_summaries=summaries,
        requirements=requirements,
    )

    timeout_seconds, max_output_tokens = _chapter_writer_limits(model)
    manifest_id = context_manifest.id
    max_output_tokens = min(max_output_tokens, max(1, context_manifest.output_reserve_tokens))
    gateway_extra = LLMGateway.local_cli_extra_body(
        model,
        base={
            "moshu_task_type": "writing",
            "moshu_project_id": project_id,
            "moshu_context_manifest_id": manifest_id,
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

    context_orchestrator.mark_consumed(context_manifest)
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
        return {
            "tool": "chapter_writer",
            "status": "skipped",
            "detail": (
                "生成期间该大纲已保存为正式章节；"
                "迟到结果未覆盖正文，也未创建无效草稿"
            ),
            "data": {
                "outline_node_id": outline_node_id,
                "existing_chapter_id": conflict.chapter.id,
            },
        }

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
            "model": result.get("model", ""),
            "context_manifest_id": manifest_id,
            "context_snapshot": context_snapshot,
        },
    }, AssistantTurnDirective.END_AFTER_DRAFT)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _manifest_section_text(context_manifest, *categories: str) -> str:
    """Return exact selected manifest content for one or more categories."""
    wanted = set(categories)
    return "\n\n".join(
        item.content_excerpt
        for item in context_manifest.items
        if item.category in wanted and item.content_excerpt
    ).strip()


def _manifest_snapshot(
    orchestrator: ContextOrchestrator,
    context_manifest,
    outline_node_id: str | None,
    involved_names: list[str],
) -> dict:
    """Compact, content-free UI snapshot generated from the manifest."""
    payload = orchestrator.manifest_payload(context_manifest, include_content=False)
    return {
        "manifest_id": context_manifest.id,
        "status": context_manifest.status,
        "outline_node_id": outline_node_id,
        "involved_characters": involved_names,
        "rag_used": any(item.chunk_id for item in context_manifest.items),
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
        ],
        "warnings": payload["warnings"],
        "explanations": [item["selection_reason"] for item in payload["items"]],
    }
