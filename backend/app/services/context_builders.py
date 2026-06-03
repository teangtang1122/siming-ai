"""Context builders for AI writing endpoints."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from ..core.exceptions import ValidationError
from ..core.utils import count_words as _count_words
from ..database.models import (
    Chapter,
    ChapterSummary,
    ChapterWorldbuilding,
    Character,
    CharacterAlias,
    CharacterRelationship,
    CharacterTimeline,
    OutlineNode,
    OutlineNodeCharacter,
    WorldbuildingEntry,
)


DIMENSION_LABELS = {
    "geography": "地理", "history": "历史", "factions": "势力",
    "power_system": "规则体系", "races": "种族", "culture": "文化",
}

WORLD_CONTEXT_MAX_ENTRIES = 32
WORLD_CONTEXT_CORE_PER_DIMENSION = 2
WORLD_CONTEXT_RECENT_LIMIT = 10
WORLD_CONTEXT_RELEVANT_LIMIT = 20
WORLD_CONTEXT_ENTRY_CHAR_LIMIT = 850

WORLD_CONTEXT_STOPWORDS = {
    "一个", "一些", "以及", "已经", "正在", "如果", "没有", "需要", "用户", "要求",
    "生成", "创建", "修改", "更新", "章节", "本章", "前文", "后续", "下一章",
    "大纲", "节点", "剧情", "摘要", "角色", "世界观", "设定", "背景", "内容",
    "当前", "之前", "之后", "这个", "那个", "这里", "那里", "他们", "她们",
    "进行", "出现", "发现", "继续", "相关", "信息", "写作", "正文",
}

def _get_outline_node_or_404(
    db: Session,
    project_id: str,
    outline_node_id: Optional[str],
) -> Optional[OutlineNode]:
    if not outline_node_id:
        return None
    node = (
        db.query(OutlineNode)
        .options(
            selectinload(OutlineNode.linked_characters).selectinload(OutlineNodeCharacter.character)
        )
        .filter(OutlineNode.id == outline_node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise ValidationError("关联大纲节点必须属于当前作品")
    return node


# ---------------------------------------------------------------------------
# Shared character resolution
# ---------------------------------------------------------------------------

def _resolve_characters_with_aliases(
    db: Session,
    project_id: str,
    outline_node_id: str | None,
    involved_names: list[str],
    limit: int,
) -> tuple[list[Character], dict[str, str]]:
    """Resolve characters by outline node links, name, and alias.

    Priority: outline node linked characters → direct name match → alias match.
    Returns (characters, resolved_aliases) where resolved_aliases maps
    alias -> canonical character name for any name matched via alias.
    """
    found: list[Character] = []
    seen: set[str] = set()
    resolved_aliases: dict[str, str] = {}

    # Pass 1: outline node linked characters
    if outline_node_id:
        links = (
            db.query(OutlineNodeCharacter)
            .join(OutlineNode, OutlineNode.id == OutlineNodeCharacter.outline_node_id)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNodeCharacter.outline_node_id == outline_node_id,
            )
            .all()
        )
        for link in links:
            if link.character and link.character.id not in seen:
                found.append(link.character)
                seen.add(link.character.id)

    # Pass 2: direct name match
    matched_names: set[str] = set()
    for name in involved_names:
        character = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name == name)
            .first()
        )
        if character and character.id not in seen:
            found.append(character)
            seen.add(character.id)
            matched_names.add(name)

    # Pass 3: alias match for unmatched names
    unmatched_names = [n for n in involved_names if n not in matched_names]
    if unmatched_names:
        aliases = (
            db.query(CharacterAlias)
            .filter(
                CharacterAlias.project_id == project_id,
                CharacterAlias.alias.in_(unmatched_names),
            )
            .all()
        )
        for alias in aliases:
            char = alias.character
            if char and char.id not in seen:
                found.append(char)
                seen.add(char.id)
                resolved_aliases[alias.alias] = char.name

    return found[:limit], resolved_aliases


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _build_world_context(
    db: Session,
    project_id: str,
    outline_node_id: Optional[str] = None,
    query_context: str = "",
    max_entries: int = WORLD_CONTEXT_MAX_ENTRIES,
    use_rag: bool = False,
) -> str:
    # Optional RAG branch: when entries > 50 and RAG is available, use FTS search
    if use_rag and query_context:
        try:
            from .rag.retriever import search_chunks
            entry_count = db.query(WorldbuildingEntry).filter(
                WorldbuildingEntry.project_id == project_id
            ).count()
            if entry_count > 50:
                results = search_chunks(
                    db, project_id, query_context,
                    source_types=["worldbuilding"],
                    limit=max_entries,
                )
                if results:
                    lines = [f"已从 {entry_count} 条世界观中通过RAG检索筛选 {len(results)} 条："]
                    for r in results:
                        dim = (r.metadata or {}).get("dimension", "")
                        dim_label = DIMENSION_LABELS.get(dim, dim)
                        content = r.content[:WORLD_CONTEXT_ENTRY_CHAR_LIMIT]
                        lines.append(f"[RAG命中, score={r.score:.1f}][{dim_label}] {r.title}: {content}")
                    return "\n".join(lines)
        except Exception:
            pass  # Fall through to legacy logic

    all_entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension.asc(), WorldbuildingEntry.sort_order.asc())
        .all()
    )
    if not all_entries:
        return "暂无世界观设定。"

    outline_texts: list[str] = []
    explicit_ids: set[str] = set()
    if outline_node_id:
        outline = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.id == outline_node_id)
            .first()
        )
        if outline:
            outline_texts.extend([outline.title or "", outline.summary or "", outline.actual_summary or "", outline.planned_summary or ""])
            if outline.source_chapter_id:
                explicit_ids.update(_worldbuilding_ids_for_chapters(db, [outline.source_chapter_id]))
            chapter_ids = [
                row.id
                for row in db.query(Chapter.id)
                .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline.id)
                .all()
            ]
            explicit_ids.update(_worldbuilding_ids_for_chapters(db, chapter_ids))

    recent_summary_texts = _recent_summary_texts(db, project_id, limit=4)
    terms = _extract_world_context_terms(query_context, *outline_texts, *recent_summary_texts)
    score_by_id = {entry.id: _worldbuilding_relevance_score(entry, terms) for entry in all_entries}

    selected: list[WorldbuildingEntry] = []
    reason_by_id: dict[str, str] = {}

    def add_entries(entries: list[WorldbuildingEntry], reason: str) -> None:
        for entry in entries:
            if len(selected) >= max_entries:
                return
            if entry.id in reason_by_id:
                continue
            selected.append(entry)
            reason_by_id[entry.id] = reason

    explicit_entries = [entry for entry in all_entries if entry.id in explicit_ids]
    add_entries(_sort_entries_by_recent(explicit_entries), "显式关联")

    relevant_entries = [
        entry for entry in all_entries
        if entry.id not in reason_by_id and score_by_id.get(entry.id, 0) > 0
    ]
    relevant_entries.sort(
        key=lambda entry: (
            score_by_id.get(entry.id, 0),
            _entry_time(entry),
            -(entry.sort_order or 0),
        ),
        reverse=True,
    )
    add_entries(relevant_entries[:WORLD_CONTEXT_RELEVANT_LIMIT], "相关命中")

    recent_entries = [
        entry for entry in _sort_entries_by_recent(all_entries)
        if entry.id not in reason_by_id
    ][:WORLD_CONTEXT_RECENT_LIMIT]
    add_entries(recent_entries, "最近更新")

    core_entries: list[WorldbuildingEntry] = []
    for dimension in DIMENSION_LABELS:
        dim_entries = [
            entry for entry in all_entries
            if entry.dimension == dimension and entry.id not in reason_by_id
        ]
        dim_entries.sort(key=lambda entry: (entry.sort_order or 0, entry.created_at or datetime.min))
        core_entries.extend(dim_entries[:WORLD_CONTEXT_CORE_PER_DIMENSION])
    add_entries(core_entries, "基础设定")

    if not selected:
        add_entries(all_entries[:min(max_entries, 24)], "基础设定")

    lines = []
    lines.append(f"已从 {len(all_entries)} 条世界观中筛选 {len(selected)} 条：")
    for entry in selected:
        dim_label = DIMENSION_LABELS.get(entry.dimension, entry.dimension)
        reason = reason_by_id.get(entry.id, "相关")
        content = (entry.content or "")[:WORLD_CONTEXT_ENTRY_CHAR_LIMIT]
        lines.append(f"[{reason}][{dim_label}] {entry.title}: {content}")
    return "\n".join(lines)


def _worldbuilding_ids_for_chapters(db: Session, chapter_ids: list[str]) -> set[str]:
    if not chapter_ids:
        return set()
    return {
        row.worldbuilding_entry_id
        for row in db.query(ChapterWorldbuilding.worldbuilding_entry_id)
        .filter(ChapterWorldbuilding.chapter_id.in_(chapter_ids))
        .all()
    }


def _recent_summary_texts(db: Session, project_id: str, limit: int = 4) -> list[str]:
    summaries = (
        db.query(ChapterSummary)
        .join(Chapter, Chapter.id == ChapterSummary.chapter_id)
        .filter(Chapter.project_id == project_id)
        .all()
    )
    if not summaries:
        return []
    summaries.sort(
        key=lambda item: (
            _chapter_order_number(item.chapter.title if item.chapter else "") or 0,
            item.chapter.created_at if item.chapter else item.updated_at,
        )
    )
    return [
        f"{item.chapter.title if item.chapter else ''}\n{item.summary_text or ''}\n{item.key_events or ''}"
        for item in summaries[-limit:]
    ]


def _extract_world_context_terms(*texts: str) -> set[str]:
    joined = "\n".join(text for text in texts if text)
    terms: set[str] = set()
    for term in re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9_-]{2,30}", joined):
        value = term.strip()
        if not value or value in WORLD_CONTEXT_STOPWORDS:
            continue
        if value.isdigit():
            continue
        terms.add(value.lower() if value.isascii() else value)
    return set(sorted(terms, key=lambda item: (-len(item), item))[:100])


def _worldbuilding_relevance_score(entry: WorldbuildingEntry, terms: set[str]) -> int:
    if not terms:
        return 0
    title = entry.title or ""
    content = (entry.content or "")[:5000]
    title_cmp = title.lower()
    content_cmp = content.lower()
    score = 0
    for term in terms:
        term_cmp = term.lower()
        if term in title or term_cmp in title_cmp:
            score += 8
        elif term in content or term_cmp in content_cmp:
            score += 2
    return score


def _entry_time(entry: WorldbuildingEntry) -> datetime:
    return entry.updated_at or entry.created_at or datetime.min


def _sort_entries_by_recent(entries: list[WorldbuildingEntry]) -> list[WorldbuildingEntry]:
    return sorted(entries, key=lambda entry: (_entry_time(entry), entry.sort_order or 0), reverse=True)


def _chinese_number_to_int(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digit_map = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    unit_map = {"十": 10, "百": 100, "千": 1000}
    total = 0
    current = 0
    for char in text:
        if char in digit_map:
            current = digit_map[char]
        elif char in unit_map:
            unit = unit_map[char]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
    total += current
    return total or None


def _chapter_order_number(title: str) -> Optional[int]:
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百千万零〇]+)\s*章", title or "")
    if not match:
        match = re.search(r"([0-9]+)", title or "")
    return _chinese_number_to_int(match.group(1)) if match else None


def _build_recent_summaries(db: Session, project_id: str, limit: int = 5) -> str:
    summaries = (
        db.query(ChapterSummary)
        .join(Chapter, Chapter.id == ChapterSummary.chapter_id)
        .filter(Chapter.project_id == project_id)
        .all()
    )
    if not summaries:
        return "暂无前文章节摘要。"
    summaries.sort(
        key=lambda item: (
            _chapter_order_number(item.chapter.title if item.chapter else "") or 0,
            item.chapter.created_at if item.chapter else item.updated_at,
        )
    )
    summaries = summaries[-limit:] if limit else []
    lines = []
    for s in summaries:
        title = s.chapter.title if s.chapter else "未知章节"
        lines.append(f"- {title}: {s.summary_text[:600]}")
    return "\n".join(lines)


def _build_outline_context(db: Session, project_id: str, outline_node_id: Optional[str]) -> str:
    node = _get_outline_node_or_404(db, project_id, outline_node_id)
    if not node:
        return "暂无当前大纲节点。"
    parts = [f"大纲节点：{node.title}（{node.node_type}）[ID: {node.id}]"]
    if node.summary:
        parts.append(f"概要：{node.summary}")
    linked = node.linked_characters
    if linked:
        char_names = [lc.character.name for lc in linked if lc.character]
        parts.append(f"涉及角色：{', '.join(char_names)}")
    return "\n".join(parts)


def _build_scene_characters_context(db: Session, project_id: str, outline_node_id: Optional[str]) -> str:
    if not outline_node_id:
        return ""
    links = (
        db.query(OutlineNodeCharacter)
        .join(OutlineNode, OutlineNode.id == OutlineNodeCharacter.outline_node_id)
        .filter(
            OutlineNodeCharacter.outline_node_id == outline_node_id,
            OutlineNode.project_id == project_id,
        )
        .all()
    )
    if not links:
        return ""
    lines = ["当前场景角色："]
    for link in links:
        char = link.character
        if char:
            role_label = link.role_in_scene or "在场"
            lines.append(
                f"- {char.name}（{char.role_type or '未分类'}，{role_label}）: "
                f"{(char.personality or '')[:200]}"
            )
    return "\n".join(lines)


def _build_character_context(character: Character) -> str:
    parts = [
        f"角色名称：{character.name}",
        f"角色类型：{character.role_type or '未分类'}",
    ]
    if character.appearance:
        parts.append(f"外貌：{character.appearance}")
    if character.personality:
        parts.append(f"性格：{character.personality}")
    if character.background:
        parts.append(f"背景：{character.background}")
    if character.abilities:
        try:
            abilities = json.loads(character.abilities)
            if isinstance(abilities, list) and abilities:
                parts.append(f"能力：{', '.join(abilities)}")
        except (json.JSONDecodeError, TypeError):
            pass
    return "\n".join(parts)


def _build_character_ai_context(character: Character) -> str:
    config = character.ai_config
    if not config:
        return ""
    parts = [f"语气风格：{config.tone_style or 'neutral'}"]
    if config.catchphrases:
        try:
            phrases = json.loads(config.catchphrases)
            if isinstance(phrases, list) and phrases:
                parts.append(f"口头禅：{', '.join(phrases)}")
        except (json.JSONDecodeError, TypeError):
            pass
    parts.append(f"话量偏好：{config.verbosity or 'moderate'}")
    parts.append(f"情感倾向：{config.emotion_tendency or 'neutral'}")
    if config.custom_system_prompt:
        parts.append(f"额外提示：{config.custom_system_prompt}")
    return "\n".join(parts)


def _build_character_relationships(db: Session, project_id: str, character_id: str) -> str:
    rels = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            (
                (CharacterRelationship.character_a_id == character_id)
                | (CharacterRelationship.character_b_id == character_id)
            ),
        )
        .all()
    )
    if not rels:
        return "暂无角色关系。"
    lines = []
    for rel in rels:
        other_id = rel.character_b_id if rel.character_a_id == character_id else rel.character_a_id
        other = db.query(Character).filter(Character.id == other_id).first()
        other_name = other.name if other else other_id[:8]
        lines.append(f"- 与{other_name}：{rel.relationship_type}" + (f"（{rel.description}）" if rel.description else ""))
    return "\n".join(lines)


def _build_character_timeline(db: Session, character_id: str, limit: int = 10) -> str:
    events = (
        db.query(CharacterTimeline)
        .filter(CharacterTimeline.character_id == character_id)
        .order_by(CharacterTimeline.created_at.desc())
        .limit(limit)
        .all()
    )
    if not events:
        return "暂无近期经历。"
    lines = ["近期经历："]
    for event in reversed(events):
        emo = f"（情感变化：{event.emotional_state_change}）" if event.emotional_state_change else ""
        lines.append(f"- [{event.event_type}] {event.event_description}{emo}")
    return "\n".join(lines)
def _build_outline_overview(db: Session, project_id: str, limit: int = 60) -> str:
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .limit(limit)
        .all()
    )
    if not nodes:
        return "暂无大纲。"
    node_by_id = {node.id: node for node in nodes}

    def path_for(node: OutlineNode) -> str:
        titles = [node.title]
        parent = node_by_id.get(node.parent_id) if node.parent_id else None
        visited = {node.id}
        while parent and parent.id not in visited:
            visited.add(parent.id)
            titles.append(parent.title)
            parent = node_by_id.get(parent.parent_id) if parent.parent_id else None
        return " / ".join(reversed(titles))

    lines = []
    for node in nodes:
        summary = f"：{node.summary[:220]}" if node.summary else ""
        lines.append(f"- [{node.id}] {path_for(node)}（{node.node_type}，{node.status or 'pending'}）{summary}")
    return "\n".join(lines)


def _build_character_catalog(db: Session, project_id: str, limit: int = 30) -> str:
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .order_by(Character.role_type.asc(), Character.updated_at.desc())
        .limit(limit)
        .all()
    )
    if not characters:
        return "暂无角色档案。"
    lines = []
    for character in characters:
        parts = [
            f"- {character.name}（{character.role_type or '未分类'}）",
            (character.personality or "")[:180],
        ]
        if character.background:
            parts.append(f"背景：{character.background[:180]}")
        if character.appearance:
            parts.append(f"外貌：{character.appearance[:120]}")
        lines.append("；".join(part for part in parts if part))
    return "\n".join(lines)


def _build_relationship_context(db: Session, project_id: str, limit: int = 50) -> str:
    rels = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project_id)
        .order_by(CharacterRelationship.created_at.asc())
        .limit(limit)
        .all()
    )
    if not rels:
        return "暂无角色关系。"
    characters = {
        character.id: character.name
        for character in db.query(Character).filter(Character.project_id == project_id).all()
    }
    lines = []
    for rel in rels:
        a = characters.get(rel.character_a_id, rel.character_a_id[:8])
        b = characters.get(rel.character_b_id, rel.character_b_id[:8])
        detail = f"：{rel.description[:220]}" if rel.description else ""
        lines.append(f"- {a} -> {b}：{rel.relationship_type}{detail}")
    return "\n".join(lines)


def _build_chapter_detail_context(
    db: Session,
    project_id: str,
    chapter_id: Optional[str],
    max_chars: int = 12000,
) -> str:
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    chapter = query.filter(Chapter.id == chapter_id).first() if chapter_id else None
    if not chapter:
        chapter = query.order_by(Chapter.created_at.desc()).first()
    if not chapter:
        return "暂无章节正文。"
    content = chapter.content or ""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n……（后续正文已截断）"
    return f"章节：{chapter.title}\n字数：{chapter.word_count or _count_words(chapter.content or '')}\n正文：\n{content}"


def _ordered_project_chapters(db: Session, project_id: str) -> list[Chapter]:
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).all()
    chapters.sort(
        key=lambda item: (
            _chapter_order_number(item.title) or 0,
            item.created_at,
        )
    )
    return chapters


def _build_recent_chapter_details(
    db: Session,
    project_id: str,
    limit: int = 8,
    max_chars_each: int = 2200,
) -> str:
    chapters = _ordered_project_chapters(db, project_id)
    if not chapters:
        return "暂无章节正文。"
    sections = []
    for chapter in chapters[-limit:]:
        content = chapter.content or ""
        if len(content) > max_chars_each:
            content = content[:max_chars_each] + "\n……（本章后续正文已截断）"
        summary = chapter.summary.summary_text if chapter.summary else ""
        sections.append(
            f"【{chapter.title}】\n"
            f"摘要：{summary[:800] if summary else '暂无'}\n"
            f"正文片段：\n{content}"
        )
    return "\n\n".join(sections)

__all__ = [
    "_get_outline_node_or_404",
    "_resolve_characters_with_aliases",
    "_build_world_context",
    "_chinese_number_to_int",
    "_chapter_order_number",
    "_build_recent_summaries",
    "_build_outline_context",
    "_build_scene_characters_context",
    "_build_character_context",
    "_build_character_ai_context",
    "_build_character_relationships",
    "_build_character_timeline",
    "_count_words",
    "_build_outline_overview",
    "_build_character_catalog",
    "_build_relationship_context",
    "_build_chapter_detail_context",
    "_ordered_project_chapters",
    "_build_recent_chapter_details",
]
