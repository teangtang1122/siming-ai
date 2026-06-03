"""Workspace tool for showing the exact context selected before writing."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import (
    Character,
    CharacterAlias,
    CharacterRelationship,
    Chapter,
    OutlineNode,
)
from ....services.context_builders import (
    _build_outline_context,
    _build_recent_summaries,
    _build_world_context,
    _recent_summary_texts,
    _resolve_characters_with_aliases,
)
from ....services.rag.context_packer import ContextBudget, PackedContext, pack_context
from ....services.rag.indexer import project_has_chunks, reindex_project_types


def _text_preview(text: str | None, limit: int = 500) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _abilities(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _character_state_payload(character: Character, aliases: list[str]) -> dict:
    return {
        "id": character.id,
        "name": character.name,
        "aliases": aliases,
        "role_type": character.role_type,
        "life_status": character.life_status or "",
        "current_location": character.current_location or "",
        "realm_or_level": character.realm_or_level or "",
        "physical_state": _text_preview(character.physical_state, 260),
        "mental_state": _text_preview(character.mental_state, 260),
        "current_goal": _text_preview(character.current_goal, 260),
        "active_conflict": _text_preview(character.active_conflict, 260),
        "abilities_state": _text_preview(character.abilities_state, 260),
        "items_or_assets": _text_preview(character.items_or_assets, 260),
        "personality": _text_preview(character.personality, 360),
        "background": _text_preview(character.background, 500),
        "abilities": _abilities(character.abilities),
        "last_seen_chapter_id": character.last_seen_chapter_id,
        "last_updated_chapter_id": character.last_updated_chapter_id,
    }


def _relationship_payload(db: Session, project_id: str, character_ids: list[str]) -> list[dict]:
    if not character_ids:
        return []
    rels = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            CharacterRelationship.character_a_id.in_(character_ids)
            | CharacterRelationship.character_b_id.in_(character_ids),
        )
        .limit(40)
        .all()
    )
    ids = set(character_ids)
    for rel in rels:
        ids.add(rel.character_a_id)
        ids.add(rel.character_b_id)
    names = {c.id: c.name for c in db.query(Character).filter(Character.id.in_(ids)).all()}
    return [
        {
            "source": names.get(rel.character_a_id, rel.character_a_id[:8]),
            "target": names.get(rel.character_b_id, rel.character_b_id[:8]),
            "relationship_type": rel.relationship_type,
            "description": _text_preview(rel.description, 260),
        }
        for rel in rels
    ]


def _recent_chapter_refs(db: Session, project_id: str, limit: int) -> list[dict]:
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": chapter.id,
            "title": chapter.title,
            "outline_node_id": chapter.outline_node_id,
            "word_count": chapter.word_count or 0,
            "summary": _text_preview(chapter.summary.summary_text if chapter.summary else "", 500),
        }
        for chapter in reversed(chapters)
    ]


async def preview_writing_context(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Preview the same high-level context families used by chapter_writer."""
    outline_node_id = str(args.get("outline_node_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()
    involved_names = [
        str(name).strip()
        for name in (args.get("involved_characters") if isinstance(args.get("involved_characters"), list) else [])
        if str(name).strip()
    ][:12]
    recent_limit = max(1, min(int(args.get("recent_limit") or 5), 12))
    character_limit = max(1, min(int(args.get("character_limit") or 8), 16))

    # --- Build rich query context (same as chapter_writer) ---
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
    auto_indexed = False
    if missing_types:
        stats = reindex_project_types(db, project_id, source_types=missing_types)
        auto_indexed = stats.get("total_chunks", 0) > 0

    # --- pack_context for outline / summaries / worldbuilding ---
    wb_limit = max(8, min(int(args.get("worldbuilding_limit") or 16), 32))
    budget = ContextBudget(
        max_worldbuilding_chars=wb_limit * 500,
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
    )

    # --- Extract section texts (fallback to traditional builders) ---
    outline_context = _section_text(packed, "outline") or (
        _build_outline_context(db, project_id, outline_node_id) if outline_node_id else "未指定大纲节点。"
    )
    recent_summaries = _section_text(packed, "summary") or _build_recent_summaries(db, project_id, limit=recent_limit)
    world_context = _section_text(packed, "worldbuilding") or _build_world_context(
        db, project_id, outline_node_id,
        query_context=requirements,
        max_entries=wb_limit,
    )

    # --- Character resolution with alias tracking ---
    characters, resolved_aliases = _resolve_characters_with_aliases(
        db, project_id, outline_node_id, involved_names, character_limit
    )
    aliases_by_character: dict[str, list[str]] = {character.id: [] for character in characters}
    if aliases_by_character:
        aliases = (
            db.query(CharacterAlias)
            .filter(CharacterAlias.project_id == project_id, CharacterAlias.character_id.in_(aliases_by_character.keys()))
            .all()
        )
        for alias in aliases:
            aliases_by_character.setdefault(alias.character_id, []).append(alias.alias)

    character_payloads = [
        _character_state_payload(character, aliases_by_character.get(character.id, []))
        for character in characters
    ]
    relationships = _relationship_payload(db, project_id, [character.id for character in characters])
    recent_chapters = _recent_chapter_refs(db, project_id, recent_limit)

    # --- Warnings (merge pack_context warnings + local checks) ---
    warnings: list[str] = list(packed.warnings)
    if outline_node_id and "暂无当前大纲节点" in outline_context:
        warnings.append("未找到目标大纲节点，章节写作可能会偏离规划。")
    matched_names = {c.name for c in characters} | set(resolved_aliases.keys())
    missing_names = [name for name in involved_names if name not in matched_names]
    if missing_names:
        warnings.append("部分指定角色未命中角色卡或别名：" + "、".join(missing_names))
    if not character_payloads:
        warnings.append("本次写作未命中任何角色当前状态。")
    if world_context.startswith("暂无"):
        warnings.append("本次写作没有可用世界观设定。")

    rag_used = any(s.chunk_ids for s in packed.sections)

    return {
        "tool": "preview_writing_context",
        "status": "ok",
        "detail": (
            f"写作上下文预检：{len(packed.sections)} 个分区、{packed.total_used_chars} 字符；"
            f"{len(character_payloads)} 个角色、{len(relationships)} 条关系；"
            f"{len(warnings)} 条风险"
        ),
        "data": {
            # Backward-compatible fields
            "outline_context": outline_context,
            "recent_chapters": recent_chapters,
            "recent_summaries_text": recent_summaries,
            "characters": character_payloads,
            "relationships": relationships,
            "world_context": world_context,
            "warnings": warnings,
            "requirements_preview": _text_preview(requirements, 1000),
            "resolved_aliases": resolved_aliases,
            # RAG-enriched fields
            "rag_sections": [
                {
                    "category": s.category,
                    "title": s.title,
                    "source_type": s.source_type,
                    "selection_reason": s.selection_reason,
                    "used_chars": s.used_chars,
                    "score": round(s.score, 2),
                    "chunk_count": len(s.chunk_ids),
                }
                for s in packed.sections
            ],
            "total_used_chars": packed.total_used_chars,
            "budget": packed.budget,
            "used_chars": packed.used_chars,
            "explanations": packed.explanations,
            "rag_used": rag_used,
            "fts_available": packed.fts_available,
            "auto_indexed": auto_indexed,
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
