"""Workspace tools for RAG: search_context, preview_rag_context, explain_context_selection."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ....services.rag.indexer import ensure_indexed, reindex_project, reindex_project_types, detect_fts5_available, project_has_chunks
from ....services.rag.retriever import search_chunks, get_chunks_for_source
from ....services.rag.context_packer import pack_context, ContextBudget
from ....services.context_orchestrator import ContextOrchestrator


# ---------------------------------------------------------------------------
# search_context
# ---------------------------------------------------------------------------

async def search_context(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Full-text search across all indexed content."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"tool": "search_context", "status": "skipped", "detail": "搜索词为空", "data": {"results": []}}

    source_types_raw = args.get("source_types")
    source_types = None
    if isinstance(source_types_raw, list) and source_types_raw:
        valid_types = {
            "chapter", "chapter_summary", "outline", "character",
            "character_timeline", "worldbuilding", "assistant_memory",
        }
        source_types = [st for st in source_types_raw if st in valid_types]

    limit = max(1, min(int(args.get("limit") or 20), 50))

    # Lazy index: if no chunks exist for this project (or for the requested
    # source_types), build the index first so the search has something to find.
    indexed_info = ""
    if not project_has_chunks(db, project_id, source_types=source_types):
        stats = reindex_project_types(db, project_id, source_types=source_types)
        indexed_info = f"（首次检索，已建立索引 {stats['total_chunks']} chunks）"

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.prepare(
        project_id=project_id,
        task_type=str(args.get("task_type") or "planning"),
        model=str(args.get("model") or "") or None,
        execution_route="workspace_search",
        arguments={**args, "query": query},
    )
    results = search_chunks(db, project_id, query, source_types=source_types, limit=limit)
    evidence_items = orchestrator.search_task_context(manifest, query=query, limit=limit)
    evidence_by_chunk = {item.get("chunk_id"): item for item in evidence_items if item.get("chunk_id")}

    detail = f"检索到 {len(results)} 条相关结果{indexed_info}"
    if manifest.status == "blocked_rebuild":
        detail += "（索引重建中，返回当前可用的词法检索结果）"

    return {
        "tool": "search_context",
        "status": "ok",
        "detail": detail,
        "data": {
            "manifest_id": manifest.id,
            "manifest_status": manifest.status,
            "rebuild_in_progress": manifest.status == "blocked_rebuild",
            "query": query,
            "fts_available": detect_fts5_available(db),
            "auto_indexed": bool(indexed_info),
            "results": [
                {
                    "chunk_id": r.chunk_id,
                    "source_type": r.source_type,
                    "source_id": r.source_id,
                    "title": r.title,
                    "content": r.content[:2000],
                    "metadata": r.metadata,
                    "score": round(r.score, 2),
                    "reason": r.reason,
                    "source_hash": (evidence_by_chunk.get(r.chunk_id) or {}).get("source_hash"),
                    "evidence": evidence_by_chunk.get(r.chunk_id),
                }
                for r in results
            ],
        },
    }


# ---------------------------------------------------------------------------
# preview_rag_context
# ---------------------------------------------------------------------------

async def preview_rag_context(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Preview budget-aware context assembly with explanations."""
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.prepare(
        project_id=project_id,
        task_type=str(args.get("task_type") or "writing"),
        model=str(args.get("model") or "") or None,
        execution_route="workspace_preview",
        arguments=args,
        pinned_chunk_ids=args.get("pinned_chunk_ids") if isinstance(args.get("pinned_chunk_ids"), list) else (),
    )

    budget_override = args.get("budget_override")
    budget = ContextBudget()
    if isinstance(budget_override, dict):
        for key in [
            "max_chapter_chars", "max_summary_chars", "max_character_chars",
            "max_worldbuilding_chars", "max_memory_chars", "max_outline_chars", "reserve_chars",
        ]:
            if key in budget_override:
                try:
                    setattr(budget, key, int(budget_override[key]))
                except (ValueError, TypeError):
                    pass

    pinned_chunk_ids = None
    raw_pinned = args.get("pinned_chunk_ids")
    if isinstance(raw_pinned, list) and raw_pinned:
        pinned_chunk_ids = [str(cid).strip() for cid in raw_pinned if str(cid).strip()]

    # Lazy index if no chunks exist yet
    auto_indexed = False
    if not project_has_chunks(db, project_id):
        stats = reindex_project(db, project_id)
        auto_indexed = stats["total_chunks"] > 0

    packed = pack_context(
        db, project_id,
        outline_node_id=outline_node_id,
        requirements=requirements,
        budget=budget,
        pinned_chunk_ids=pinned_chunk_ids,
    )

    detail = (
        f"上下文打包完成：{len(packed.sections)} 个分区，"
        f"共 {packed.total_used_chars} 字符；"
        f"{len(packed.warnings)} 条警告"
    )
    if auto_indexed:
        detail += "（首次调用，已自动建立索引）"

    return {
        "tool": "preview_rag_context",
        "status": "ok",
        "detail": detail,
        "data": {
            "manifest_id": manifest.id,
            "context_manifest": orchestrator.manifest_payload(manifest, include_content=True),
            "sections": [
                {
                    "category": s.category,
                    "title": s.title,
                    "content": s.content[:3000],
                    "source_type": s.source_type,
                    "source_id": s.source_id,
                    "chunk_ids": s.chunk_ids,
                    "selection_reason": s.selection_reason,
                    "score": round(s.score, 2),
                    "used_chars": s.used_chars,
                }
                for s in packed.sections
            ],
            "total_used_chars": packed.total_used_chars,
            "budget": packed.budget,
            "used_chars": packed.used_chars,
            "explanations": packed.explanations,
            "warnings": packed.warnings,
            "fts_available": packed.fts_available,
            "auto_indexed": auto_indexed,
        },
    }


# ---------------------------------------------------------------------------
# explain_context_selection
# ---------------------------------------------------------------------------

async def explain_context_selection(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Explain why specific sources would be selected for a writing task."""
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()

    source_ids_raw = args.get("source_ids")
    if not isinstance(source_ids_raw, list) or not source_ids_raw:
        return {
            "tool": "explain_context_selection",
            "status": "skipped",
            "detail": "未提供要解释的来源ID",
            "data": {"explanations": []},
        }
    source_ids = [str(sid).strip() for sid in source_ids_raw if str(sid).strip()]

    # Run pack_context to get the full selection pipeline
    packed = pack_context(db, project_id, outline_node_id=outline_node_id, requirements=requirements)

    # Build explanations for requested source_ids
    explanations: list[dict] = []
    for source_id in source_ids:
        # Find chunks for this source
        chunks = get_chunks_for_source(db, project_id, "", source_id)
        if not chunks:
            # Try looking up by source_id directly
            from ....database.models import RagChunk
            found = db.query(RagChunk).filter(
                RagChunk.project_id == project_id,
                RagChunk.source_id == source_id,
            ).first()
            if found:
                chunks = [{
                    "source_type": found.source_type,
                    "title": found.title,
                    "chunk_id": found.id,
                }]

        if not chunks:
            explanations.append({
                "source_id": source_id,
                "found": False,
                "reason": "未在RAG索引中找到此来源。可能需要先运行 reindex_project。",
            })
            continue

        # Check if this source is in the packed context
        in_context = False
        for section in packed.sections:
            if section.source_id == source_id:
                in_context = True
                explanations.append({
                    "source_id": source_id,
                    "source_type": section.source_type,
                    "title": section.title,
                    "in_context": True,
                    "category": section.category,
                    "score": round(section.score, 2),
                    "selection_reason": section.selection_reason,
                    "used_chars": section.used_chars,
                })
                break

        if not in_context:
            source_type = chunks[0].get("source_type", "") if chunks else ""
            explanations.append({
                "source_id": source_id,
                "source_type": source_type,
                "title": chunks[0].get("title", "") if chunks else "",
                "in_context": False,
                "reason": "该来源未被选入当前上下文。可能因为相关性不足或预算已满。",
            })

    return {
        "tool": "explain_context_selection",
        "status": "ok",
        "detail": f"已解释 {len(explanations)} 个来源的选取原因",
        "data": {
            "explanations": explanations,
            "pack_summary": {
                "total_sections": len(packed.sections),
                "total_used_chars": packed.total_used_chars,
                "budget": packed.budget,
                "used_chars": packed.used_chars,
                "warnings": packed.warnings,
            },
        },
    }
