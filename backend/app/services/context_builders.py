"""Context builders for AI writing endpoints."""
from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from ..core.exceptions import ValidationError
from ..database.models import (
    Chapter,
    ChapterSummary,
    Character,
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
# Context builders
# ---------------------------------------------------------------------------

def _build_world_context(db: Session, project_id: str, outline_node_id: Optional[str] = None) -> str:
    entries = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .order_by(WorldbuildingEntry.dimension.asc(), WorldbuildingEntry.sort_order.asc())
        .limit(24)
        .all()
    )
    if not entries:
        return "暂无世界观设定。"
    lines = []
    for entry in entries:
        dim_label = DIMENSION_LABELS.get(entry.dimension, entry.dimension)
        lines.append(f"[{dim_label}] {entry.title}: {entry.content[:1200]}")
    return "\n".join(lines)


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

def _count_words(text: str) -> int:
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text or "")
    without_cjk = re.sub(r"[\u4e00-\u9fff]", " ", text or "")
    latin_words = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", without_cjk)
    return len(cjk_chars) + len(latin_words)

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
