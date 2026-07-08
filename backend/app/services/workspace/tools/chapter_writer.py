"""Chapter Writer workspace tool — generates chapter body prose with full writing rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....ai.gateway import LLMGateway
from ....ai.local_cli_adapter import is_local_cli_provider
from ....core.utils import count_words
from ....database.models import (
    Character,
    CharacterRelationship,
    OutlineNode,
    Project,
)
from ....services.agent.prompt_builder import compose_chapter_writer_messages, get_chapter_pack
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
    _build_world_context,
    _recent_summary_texts,
    _resolve_characters_with_aliases,
)
from ....services.rag.context_packer import ContextBudget, PackedContext, PinnedContext, pack_context
from ....services.rag.indexer import project_has_chunks, reindex_project_types
from ....services.rag.retriever import search_chunks
from ....prompts.style_prompts import build_style_context
from ....prompts.writing_task_prompts import build_writing_directives
from ..generated_drafts import store_chapter_draft


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
        previous_plot: Optional plot design JSON from design_plot tool
        previous_roleplay: Optional roleplay results from roleplay/dialogue_battle tools

    Returns:
        {"tool": "chapter_writer", "status": "ok", "detail": "...",
         "data": {"content": "...", "word_count": N}}
    """
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()
    mode = str(args.get("mode") or "quality").strip()
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

    # --- Build rich RAG query context ---
    outline_node = None
    if outline_node_id:
        outline_node = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.id == outline_node_id)
            .first()
        )
    query_parts = [requirements]
    if outline_node:
        query_parts.extend([outline_node.title or "", outline_node.summary or ""])
        if outline_node.actual_summary:
            query_parts.append(outline_node.actual_summary)
        if outline_node.planned_summary:
            query_parts.append(outline_node.planned_summary)
    if plot_design:
        query_parts.append(str(plot_design)[:3000])
    if roleplay_results:
        query_parts.append(str(roleplay_results)[:3000])
    if involved_names:
        query_parts.append(" ".join(involved_names))
    recent_summary_texts = _recent_summary_texts(db, project_id, limit=3)
    query_parts.extend(recent_summary_texts)
    query_context = "\n".join(p for p in query_parts if p)

    # --- Lazy index per source type ---
    needed_types = ["worldbuilding", "character", "chapter_summary", "outline"]
    missing_types = [
        st for st in needed_types
        if not project_has_chunks(db, project_id, source_types=[st])
    ]
    if missing_types:
        reindex_project_types(db, project_id, source_types=missing_types)

    # --- pack_context for outline / summaries / worldbuilding (exclude characters) ---
    budget = ContextBudget(
        max_worldbuilding_chars=8000,
        max_character_chars=0,
        max_summary_chars=3000,
        max_outline_chars=2000,
    )
    packed = pack_context(
        db, project_id,
        outline_node_id=outline_node_id,
        requirements=query_context,
        budget=budget,
        include_categories={"outline", "summary", "worldbuilding"},
        pinned=PinnedContext(),
    )

    # --- Extract text from packed sections (fallback to traditional builders) ---
    outline_ctx = _section_text(packed, "outline") or (
        _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "无指定大纲节点。"
    )
    world_ctx = _section_text(packed, "worldbuilding") or _build_world_context(
        db, project_id, outline_node_id, query_context=query_context
    )
    summaries = _section_text(packed, "summary") or _build_recent_summaries(db, project_id, limit=5)

    # --- Style context ---
    style_ctx = build_style_context(project, include_anti_ai=False)
    writing_directives = build_writing_directives(
        project_title=project.title or "",
        project_description=project.description or "",
        project_tags=project.tags,
        outline_context=outline_ctx,
        world_context=world_ctx,
        requirements=requirements,
        plot_design=plot_design,
        roleplay_results=roleplay_results,
    )

    # --- Character details: name + alias resolution + RAG fallback ---
    char_detail_text, resolved_aliases, char_rag_used = _build_character_details_with_rag(
        db, project_id, outline_node_id, involved_names, query_context
    )

    # --- Enriched context_snapshot (metadata only, no full content) ---
    context_snapshot = _build_enriched_snapshot(
        packed, outline_node_id, involved_names, resolved_aliases,
        char_detail_text, char_rag_used
    )

    pack = get_chapter_pack(mode)
    messages = compose_chapter_writer_messages(
        pack=pack,
        style_context=style_ctx,
        outline_context=outline_ctx,
        world_context=world_ctx,
        character_profiles=char_detail_text,
        recent_summaries=summaries,
        plot_design=plot_design,
        roleplay_results=roleplay_results,
        requirements=requirements,
        writing_directives=writing_directives,
    )

    model = str(args.get("model") or "") or None
    provider = _chapter_writer_provider(model)
    if provider == "local_llama_cpp":
        return {
            "tool": "chapter_writer",
            "status": "error",
            "detail": (
                "当前选择的是司命本地 AI，它适合轻量对话/检索，不适合由内部 chapter_writer 生成整章正文。"
                "请切换到 API 或本机 CLI 模型后重试，或使用外部写作流程。"
            ),
            "data": {},
        }
    timeout_seconds, max_output_tokens = _chapter_writer_limits(model)

    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.8,
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            retry=1,
            extra_body={
                "moshu_task_type": "writing",
                "moshu_project_id": project_id,
            },
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

    outline_title = ""
    if outline_node_id:
        outline = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.id == outline_node_id)
            .first()
        )
        outline_title = outline.title if outline else ""
    draft_id = store_chapter_draft(
        project_id=project_id,
        content=content,
        title=outline_title,
        outline_node_id=outline_node_id,
        db=db,
    )

    return {
        "tool": "chapter_writer",
        "status": "ok",
        "detail": f"已生成章节正文（{count_words(content)} 字）",
        "data": {
            "draft_id": draft_id,
            "content_ref": draft_id,
            "content": content,
            "word_count": count_words(content),
            "model": result.get("model", ""),
            "context_snapshot": context_snapshot,
        },
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _section_text(packed: PackedContext, category: str) -> str | None:
    """Extract content string from a packed section by category."""
    for s in packed.sections:
        if s.category == category:
            return s.content
    return None


def _build_character_details_with_rag(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
    involved_names: list[str],
    query_context: str,
) -> tuple[str, dict[str, str], bool]:
    """Build character detail text with alias support and RAG fallback.

    Returns:
        (char_detail_text, resolved_aliases, rag_used)
        resolved_aliases maps alias -> canonical character name.
    """
    # Use shared resolution: outline links + name + alias
    characters, resolved_aliases = _resolve_characters_with_aliases(
        db, project_id, outline_node_id, involved_names, limit=12,
    )
    char_details = [_format_character_detail(db, project_id, c) for c in characters]
    seen_ids = {c.id for c in characters}

    # RAG fallback if no characters found and we have query context
    rag_used = False
    if not char_details and (involved_names or outline_node_id) and query_context:
        rag_results = search_chunks(
            db, project_id, query_context,
            source_types=["character"],
            limit=10,
        )
        if rag_results:
            rag_used = True
            for r in rag_results:
                if r.source_id and r.source_id not in seen_ids:
                    char = db.query(Character).filter(
                        Character.project_id == project_id,
                        Character.id == r.source_id,
                    ).first()
                    if char:
                        seen_ids.add(char.id)
                        char_details.append(_format_character_detail(db, project_id, char))

    char_detail_text = "\n\n".join(char_details) if char_details else "未指定角色。"
    return char_detail_text, resolved_aliases, rag_used


def _format_character_detail(db: Session, project_id: str, c: Character) -> str:
    """Format a single character's detail block."""
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
            detail_parts.append("  关系:\n" + "\n".join(rel_lines))
    return "\n".join(detail_parts)


def _build_enriched_snapshot(
    packed: PackedContext,
    outline_node_id: str | None,
    involved_names: list[str],
    resolved_aliases: dict[str, str],
    char_detail_text: str,
    char_rag_used: bool,
) -> dict:
    """Build context_snapshot with RAG metadata (no full content)."""
    sections_info = []
    for s in packed.sections:
        sections_info.append({
            "category": s.category,
            "title": s.title,
            "source_type": s.source_type,
            "selection_reason": s.selection_reason,
            "used_chars": s.used_chars,
            "estimated_tokens": s.estimated_tokens,
            "score": round(s.score, 2),
            "chunk_count": len(s.chunk_ids),
        })

    rag_used = any(s.chunk_ids for s in packed.sections) or char_rag_used
    warnings = list(packed.warnings)

    return {
        "outline_node_id": outline_node_id,
        "involved_characters": involved_names,
        "resolved_aliases": resolved_aliases,
        "rag_used": rag_used,
        "total_used_chars": packed.total_used_chars,
        "total_estimated_tokens": packed.total_estimated_tokens,
        "sections": sections_info,
        "explanations": packed.explanations,
        "warnings": warnings,
        "fts_available": packed.fts_available,
        "resolved_character_count": char_detail_text.count("【"),
    }
