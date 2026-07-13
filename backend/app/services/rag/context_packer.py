"""RAG context packer: budget-aware context assembly with explanations."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimate token count. ~1 token per CJK char, ~4 chars per English word."""
    if not text:
        return 0
    # Count CJK characters (each ~1 token)
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    # Count non-CJK characters (roughly 4 chars per token)
    non_cjk = len(text) - cjk_count
    return cjk_count + max(1, non_cjk // 4)

from ...database.models import (
    Chapter,
    ChapterSummary,
    Character,
    OutlineNode,
    RagChunk,
    WorldbuildingEntry,
)
from ..context_builders import (
    _build_character_context,
    _build_character_relationships,
    _build_outline_context,
    _build_recent_summaries,
    _build_world_context,
    _chapter_order_number,
)
from .indexer import detect_fts5_available, ensure_indexed
from .retriever import SearchResult, search_chunks


@dataclass
class ContextBudget:
    """Global context budget with legacy category limits.

    The original fields remain character-oriented so existing callers keep
    their behaviour.  Manifest orchestration uses the token-window fields as
    the hard source of truth; category limits are now only field-compression
    hints rather than an independent model budget.
    """
    max_system_chars: int = 0       # fixed, not adjustable
    max_input_chars: int = 0        # current user message
    max_chapter_chars: int = 5000
    max_summary_chars: int = 3000
    max_character_chars: int = 6000
    max_worldbuilding_chars: int = 8000
    max_memory_chars: int = 2000
    max_outline_chars: int = 2000
    reserve_chars: int = 4000
    token_budget: int = 6000        # soft target for estimated tokens
    context_window_tokens: int = 0
    output_reserve_tokens: int = 0
    safety_margin_tokens: int = 512
    hard_input_budget_tokens: int = 0
    task_type: str = ""

    @classmethod
    def from_token_window(
        cls,
        *,
        context_window_tokens: int,
        output_reserve_tokens: int,
        safety_margin_tokens: int = 512,
        task_type: str = "",
    ) -> "ContextBudget":
        """Build a hard global budget from a model context profile.

        ``window - output reserve - safety margin`` is intentionally computed
        once here so callers cannot accidentally allocate each category a full
        independent budget.
        """
        window = max(1, int(context_window_tokens))
        output = max(0, int(output_reserve_tokens))
        margin = max(0, int(safety_margin_tokens))
        input_budget = max(0, window - output - margin)
        return cls(
            token_budget=input_budget,
            context_window_tokens=window,
            output_reserve_tokens=output,
            safety_margin_tokens=margin,
            hard_input_budget_tokens=input_budget,
            task_type=task_type,
        )

    @property
    def total_chars(self) -> int:
        return (
            self.max_system_chars + self.max_input_chars + self.max_chapter_chars
            + self.max_summary_chars + self.max_character_chars + self.max_worldbuilding_chars
            + self.max_memory_chars + self.max_outline_chars + self.reserve_chars
        )

    def can_fit(self, used_chars: int, addition_chars: int) -> bool:
        total_budget = (
            self.max_chapter_chars + self.max_summary_chars + self.max_character_chars
            + self.max_worldbuilding_chars + self.max_memory_chars + self.max_outline_chars
        )
        return used_chars + addition_chars <= total_budget

    def can_fit_tokens(self, used_tokens: int, addition_tokens: int) -> bool:
        """Return whether an item fits the manifest-wide token ceiling."""
        limit = self.hard_input_budget_tokens or self.token_budget
        return max(0, used_tokens) + max(0, addition_tokens) <= max(0, limit)

    def remaining_tokens(self, used_tokens: int) -> int:
        """Return the remaining hard input budget for a manifest."""
        limit = self.hard_input_budget_tokens or self.token_budget
        return max(0, limit - max(0, used_tokens))

    def to_dict(self) -> dict[str, int]:
        data = {
            "max_chapter_chars": self.max_chapter_chars,
            "max_summary_chars": self.max_summary_chars,
            "max_character_chars": self.max_character_chars,
            "max_worldbuilding_chars": self.max_worldbuilding_chars,
            "max_memory_chars": self.max_memory_chars,
            "max_outline_chars": self.max_outline_chars,
            "reserve_chars": self.reserve_chars,
            "token_budget": self.token_budget,
        }
        if self.context_window_tokens:
            data.update({
                "context_window_tokens": self.context_window_tokens,
                "output_reserve_tokens": self.output_reserve_tokens,
                "safety_margin_tokens": self.safety_margin_tokens,
                "hard_input_budget_tokens": self.hard_input_budget_tokens,
            })
        return data


@dataclass
class ContextSection:
    category: str
    title: str
    content: str
    source_type: str
    source_id: str
    chunk_ids: list[str]
    selection_reason: str
    score: float
    used_chars: int
    estimated_tokens: int = 0


@dataclass
class PinnedContext:
    """Context that must not be squeezed out by budget pressure."""
    pin_outline: bool = True          # Current outline node
    pin_character_states: bool = True  # Active character states
    pin_recent_summaries: bool = True  # Last 2 chapter summaries


@dataclass
class PackedContext:
    sections: list[ContextSection]
    total_used_chars: int
    budget: dict[str, int]
    used_chars: dict[str, int]
    explanations: list[str]
    warnings: list[str]
    fts_available: bool
    total_estimated_tokens: int = 0


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _pack_outline(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
    budget: ContextBudget,
) -> tuple[ContextSection | None, str]:
    """Build outline context section."""
    if not outline_node_id:
        return None, ""

    ctx = _build_outline_context(db, project_id, outline_node_id)
    if not ctx or "暂无" in ctx:
        return None, ctx

    used = min(len(ctx), budget.max_outline_chars)
    if len(ctx) > budget.max_outline_chars:
        ctx = ctx[:budget.max_outline_chars] + "..."

    return ContextSection(
        category="outline",
        title="当前大纲节点",
        content=ctx,
        source_type="outline",
        source_id=outline_node_id,
        chunk_ids=[],
        selection_reason="当前写作目标大纲节点",
        score=100.0,
        used_chars=used,
        estimated_tokens=estimate_tokens(ctx),
    ), ctx


def _pack_summaries(
    db: Session,
    project_id: str,
    budget: ContextBudget,
) -> ContextSection:
    """Build recent summaries section."""
    ctx = _build_recent_summaries(db, project_id, limit=5)
    if "暂无" in ctx:
        return ContextSection(
            category="summary",
            title="近期章节摘要",
            content="暂无前文章节摘要。",
            source_type="chapter_summary",
            source_id="",
            chunk_ids=[],
            selection_reason="自动选取最近5章摘要",
            score=0.0,
            used_chars=0,
            estimated_tokens=0,
        )

    used = min(len(ctx), budget.max_summary_chars)
    if len(ctx) > budget.max_summary_chars:
        ctx = ctx[:budget.max_summary_chars] + "..."

    return ContextSection(
        category="summary",
        title="近期章节摘要",
        content=ctx,
        source_type="chapter_summary",
        source_id="",
        chunk_ids=[],
        selection_reason="自动选取最近5章摘要",
        score=90.0,
        used_chars=used,
        estimated_tokens=estimate_tokens(ctx),
    )


def _pack_worldbuilding(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
    requirements: str,
    budget: ContextBudget,
    rag_available: bool,
) -> tuple[ContextSection, list[SearchResult]]:
    """Build worldbuilding section, using RAG when available and entries > 50."""
    rag_attempted = False
    rag_results: list[SearchResult] = []

    if rag_available:
        entry_count = db.query(WorldbuildingEntry).filter(
            WorldbuildingEntry.project_id == project_id,
        ).count()
        if entry_count > 50:
            rag_attempted = True
            rag_results = search_chunks(
                db, project_id, requirements,
                source_types=["worldbuilding"],
                limit=32,
            )

    if rag_attempted and rag_results:
        sections_text: list[str] = []
        total_used = 0
        chunk_ids: list[str] = []
        for r in rag_results:
            if total_used + len(r.content) > budget.max_worldbuilding_chars:
                break
            sections_text.append(f"[score={r.score:.1f}, {r.reason}] {r.title}: {r.content[:850]}")
            total_used += len(r.content)
            chunk_ids.append(r.chunk_id)

        content = "\n".join(sections_text)
        return ContextSection(
            category="worldbuilding",
            title="世界观设定",
            content=content,
            source_type="worldbuilding",
            source_id="",
            chunk_ids=chunk_ids,
            selection_reason=f"RAG检索({len(rag_results)}条命中，{len(chunk_ids)}条入选)",
            score=sum(r.score for r in rag_results[:len(chunk_ids)]),
            used_chars=total_used,
            estimated_tokens=estimate_tokens(content),
        ), rag_results

    if rag_attempted and not rag_results:
        # RAG was tried but found nothing — fall through to legacy with clear reason
        ctx = _build_world_context(db, project_id, outline_node_id, query_context=requirements)
        used = min(len(ctx), budget.max_worldbuilding_chars)
        if len(ctx) > budget.max_worldbuilding_chars:
            ctx = ctx[:budget.max_worldbuilding_chars] + "..."
        return ContextSection(
            category="worldbuilding",
            title="世界观设定",
            content=ctx,
            source_type="worldbuilding",
            source_id="",
            chunk_ids=[],
            selection_reason="RAG检索无命中，回退传统关键词筛选",
            score=80.0,
            used_chars=used,
            estimated_tokens=estimate_tokens(ctx),
        ), rag_results

    # Traditional path (entries <= 50 or RAG unavailable)
    ctx = _build_world_context(db, project_id, outline_node_id, query_context=requirements)
    used = min(len(ctx), budget.max_worldbuilding_chars)
    if len(ctx) > budget.max_worldbuilding_chars:
        ctx = ctx[:budget.max_worldbuilding_chars] + "..."

    entry_count = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).count()
    return ContextSection(
        category="worldbuilding",
        title="世界观设定",
        content=ctx,
        source_type="worldbuilding",
        source_id="",
        chunk_ids=[],
        selection_reason=f"传统关键词筛选（{entry_count}条）",
        score=80.0,
        used_chars=used,
        estimated_tokens=estimate_tokens(ctx),
    ), rag_results


def _pack_characters(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
    requirements: str,
    budget: ContextBudget,
    rag_available: bool,
) -> tuple[ContextSection, list[SearchResult]]:
    """Build character section with RAG when available."""
    rag_results: list[SearchResult] = []

    if rag_available and requirements:
        rag_results = search_chunks(
            db, project_id, requirements,
            source_types=["character"],
            limit=10,
        )

    if rag_results:
        sections_text: list[str] = []
        total_used = 0
        chunk_ids: list[str] = []
        seen_sources: set[str] = set()
        for r in rag_results:
            if total_used + len(r.content) > budget.max_character_chars:
                break
            if r.source_id not in seen_sources:
                sections_text.append(f"[{r.title}] {r.content[:1200]}")
                total_used += len(r.content)
                chunk_ids.append(r.chunk_id)
                seen_sources.add(r.source_id)

        content = "\n\n".join(sections_text)
        return ContextSection(
            category="characters",
            title="相关角色",
            content=content,
            source_type="character",
            source_id="",
            chunk_ids=chunk_ids,
            selection_reason=f"RAG检索({len(rag_results)}条命中，{len(chunk_ids)}个角色入选)",
            score=sum(r.score for r in rag_results[:len(chunk_ids)]),
            used_chars=total_used,
            estimated_tokens=estimate_tokens(content),
        ), rag_results

    if outline_node_id:
        node = db.query(OutlineNode).filter(OutlineNode.id == outline_node_id).first()
        if node:
            from ...database.models import OutlineNodeCharacter
            links = (
                db.query(OutlineNodeCharacter)
                .filter(OutlineNodeCharacter.outline_node_id == outline_node_id)
                .all()
            )
            parts: list[str] = []
            total_used = 0
            for link in links:
                char = link.character if link else None
                if not char:
                    continue
                char_ctx = _build_character_context(char)
                rels_ctx = _build_character_relationships(db, project_id, char.id)
                section = f"{char_ctx}\n{rels_ctx}"
                if total_used + len(section) > budget.max_character_chars:
                    break
                parts.append(section)
                total_used += len(section)

            if parts:
                content = "\n\n".join(parts)
                return ContextSection(
                    category="characters",
                    title="场景角色",
                    content=content,
                    source_type="character",
                    source_id="",
                    chunk_ids=[],
                    selection_reason="大纲节点关联角色",
                    score=95.0,
                    used_chars=total_used,
                    estimated_tokens=estimate_tokens(content),
                ), []

    return ContextSection(
        category="characters",
        title="相关角色",
        content="暂无相关角色。",
        source_type="character",
        source_id="",
        chunk_ids=[],
        selection_reason="未找到相关角色",
        score=0.0,
        used_chars=0,
        estimated_tokens=0,
    ), []


def _pack_pinned(
    db: Session,
    pinned_chunk_ids: list[str],
    budget: ContextBudget,
    used_chars: int,
) -> tuple[list[ContextSection], int]:
    """Force-include pinned chunks."""
    if not pinned_chunk_ids:
        return [], used_chars

    sections: list[ContextSection] = []
    for cid in pinned_chunk_ids:
        chunk = db.query(RagChunk).filter(RagChunk.id == cid).first()
        if not chunk:
            continue
        section = ContextSection(
            category="pinned",
            title=f"固定: {chunk.title or chunk.source_type}",
            content=chunk.content or "",
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            chunk_ids=[cid],
            selection_reason="用户固定选取",
            score=999.0,
            used_chars=len(chunk.content or ""),
            estimated_tokens=estimate_tokens(chunk.content or ""),
        )
        sections.append(section)
        used_chars += len(chunk.content or "")

    return sections, used_chars


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pack_context(
    db: Session,
    project_id: str,
    outline_node_id: str | None = None,
    requirements: str = "",
    budget: ContextBudget | None = None,
    pinned_chunk_ids: list[str] | None = None,
    include_categories: set[str] | None = None,
    pinned: PinnedContext | None = None,
) -> PackedContext:
    """Budget-aware context assembly with full explanations.

    Args:
        include_categories: If provided, only build sections for these categories.
            None (default) builds all categories. Valid values:
            "outline", "summary", "characters", "worldbuilding", "pinned".
        pinned: If provided, pins certain context types so they are allocated
            budget first and cannot be squeezed out.
    """
    if budget is None:
        budget = ContextBudget()

    fts_available = detect_fts5_available(db)
    rag_available = fts_available or True  # LIKE fallback always available

    sections: list[ContextSection] = []
    explanations: list[str] = []
    warnings: list[str] = []
    used_by_category: dict[str, int] = {}

    def _should_include(category: str) -> bool:
        return include_categories is None or category in include_categories

    # 1. Outline (priority 1)
    if _should_include("outline"):
        outline_section, outline_ctx = _pack_outline(db, project_id, outline_node_id, budget)
        if outline_section:
            # Pin outline when PinnedContext requests it
            if pinned and pinned.pin_outline:
                outline_section.score = 999.0
            sections.append(outline_section)
            used_by_category["outline"] = outline_section.used_chars
            explanations.append(f"大纲节点：{outline_section.selection_reason}")

    # 2. Recent summaries
    if _should_include("summary"):
        summary_section = _pack_summaries(db, project_id, budget)
        # Pin recent summaries when PinnedContext requests it
        if pinned and pinned.pin_recent_summaries and summary_section.used_chars > 0:
            summary_section.score = 998.0
        sections.append(summary_section)
        used_by_category["summary"] = summary_section.used_chars
        explanations.append(f"章节摘要：{summary_section.selection_reason}")

    # 3. Characters
    if _should_include("characters"):
        char_section, char_rag = _pack_characters(db, project_id, outline_node_id, requirements, budget, rag_available)
        # Pin character states when PinnedContext requests it
        if pinned and pinned.pin_character_states and char_section.used_chars > 0:
            char_section.score = 997.0
        sections.append(char_section)
        used_by_category["characters"] = char_section.used_chars
        explanations.append(f"角色：{char_section.selection_reason}")

    # 4. Worldbuilding
    if _should_include("worldbuilding"):
        wb_section, wb_rag = _pack_worldbuilding(db, project_id, outline_node_id, requirements, budget, rag_available)
        sections.append(wb_section)
        used_by_category["worldbuilding"] = wb_section.used_chars
        explanations.append(f"世界观：{wb_section.selection_reason}")

    # 5. Pinned chunks (force-include)
    total_used = sum(used_by_category.values())
    if pinned_chunk_ids and _should_include("pinned"):
        pinned_sections, total_used = _pack_pinned(db, pinned_chunk_ids, budget, total_used)
        sections.extend(pinned_sections)
        used_by_category["pinned"] = sum(s.used_chars for s in pinned_sections)
        explanations.append(f"固定选取：{len(pinned_sections)}个内容块")

    total_used_chars = sum(s.used_chars for s in sections)
    total_estimated_tokens = sum(s.estimated_tokens for s in sections)

    # --- Warnings ---
    if not fts_available:
        warnings.append("FTS5不可用，使用LIKE降级搜索。建议升级SQLite版本。")
    if total_used_chars > budget.total_chars - budget.reserve_chars:
        warnings.append(f"上下文已接近预算上限({total_used_chars}/{budget.total_chars - budget.reserve_chars}字符)。")
    if not outline_node_id and _should_include("outline"):
        warnings.append("未指定大纲节点，上下文可能不够精准。")

    # Missing context warnings
    if _should_include("characters"):
        char_count = db.query(Character).filter(Character.project_id == project_id).count()
        char_section = next((s for s in sections if s.category == "characters"), None)
        if char_count > 0 and (char_section is None or char_section.used_chars == 0):
            warnings.append("项目有角色数据但未找到相关角色资料，可能影响写作质量。")
        elif char_section and char_section.used_chars == 0:
            warnings.append("未找到任何相关角色信息。")

    if outline_node_id and _should_include("outline"):
        outline_section = next((s for s in sections if s.category == "outline"), None)
        if outline_section is None:
            warnings.append("指定了大纲节点但未找到对应大纲资料。")

    # RAG miss warnings — only for included categories
    if _should_include("worldbuilding"):
        wb_section = next((s for s in sections if s.category == "worldbuilding"), None)
        if wb_section:
            wb_count = db.query(WorldbuildingEntry).filter(
                WorldbuildingEntry.project_id == project_id,
            ).count()
            if wb_count > 50:
                reason = wb_section.selection_reason
                if "RAG检索无命中" in reason:
                    warnings.append(f"世界观有 {wb_count} 条，RAG检索未命中任何设定，已回退传统筛选。")
                elif "RAG检索" in reason and wb_section.used_chars < 200:
                    warnings.append("RAG检索世界观命中过少，上下文可能不充分。")

    return PackedContext(
        sections=sections,
        total_used_chars=total_used_chars,
        budget=budget.to_dict(),
        used_chars=used_by_category,
        explanations=explanations,
        warnings=warnings,
        fts_available=fts_available,
        total_estimated_tokens=total_estimated_tokens,
    )
