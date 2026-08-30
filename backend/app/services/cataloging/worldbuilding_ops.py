"""Worldbuilding cataloging writes."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    Chapter,
    WorldbuildingEntry,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from .candidate_io import float_or_none
from .constants import WORLD_DIMENSIONS
from .links import link_chapter_worldbuilding
from .lookups import find_worldbuilding_by_title_or_id, next_worldbuilding_sort_order
from .merge import merge_text
from .snapshots import chapter_change_title, worldbuilding_snapshot

PLACEHOLDER_WORLDBUILDING_TITLES = {"未命名", "未命名设定", "未知", "无标题", "设定名"}


def apply_worldbuilding(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
    create: bool,
) -> dict:
    title = str(payload.get("title") or "").strip()
    if not title or _is_placeholder_worldbuilding_title(title):
        raise ValueError("世界观标题为空")
    content = str(payload.get("content") or payload.get("description") or payload.get("evidence") or candidate.evidence or "").strip()
    if not content:
        raise ValueError("世界观内容为空")
    dimension = _normalize_dimension(payload.get("dimension"), payload)
    entry = find_worldbuilding_by_title_or_id(db, chapter.project_id, payload.get("id") or title)
    old = worldbuilding_snapshot(entry) if entry else None
    if not entry:
        entry = WorldbuildingEntry(
            project_id=chapter.project_id,
            dimension=dimension,
            title=title[:200],
            content=content[:12000],
            sort_order=next_worldbuilding_sort_order(db, chapter.project_id, dimension),
            first_seen_chapter_id=chapter.id,
            last_updated_chapter_id=chapter.id,
            status="active",
            confidence=float_or_none(candidate.confidence),
        )
        db.add(entry)
        db.flush()
    else:
        entry.dimension = dimension
        if content:
            previous = payload.get("_cataloging_previous_payload")
            previous = previous if isinstance(previous, dict) else {}
            previous_content = str(
                previous.get("content")
                or previous.get("description")
                or previous.get("evidence")
                or ""
            ).strip()
            current_content = str(entry.content or "")
            entry.content = (
                current_content.replace(previous_content, content, 1)[:12000]
                if previous_content and previous_content in current_content
                else merge_text(current_content, content, chapter, limit=12000)
            )
        entry.title = title[:200]
        if _chapter_can_advance_world_state(db, entry, chapter):
            entry.last_updated_chapter_id = chapter.id
        entry.confidence = float_or_none(candidate.confidence) or entry.confidence
    if old is None or worldbuilding_snapshot(entry) != old:
        ensure_worldbuilding_version(db, entry, chapter, payload)
    link_chapter_worldbuilding(
        db,
        chapter,
        entry,
        str(payload.get("description") or payload.get("evidence") or ""),
    )
    return {
        "target_type": "worldbuilding",
        "target_id": entry.id,
        "old_value": old,
        "new_value": worldbuilding_snapshot(entry),
        "detail": f"世界观已写入: {entry.title}",
    }


def apply_worldbuilding_timeline(db: Session, candidate: CatalogingCandidate, chapter: Chapter, payload: dict[str, Any]) -> dict:
    entry = find_worldbuilding_by_title_or_id(db, chapter.project_id, payload.get("id") or payload.get("title"))
    if not entry:
        title = str(payload.get("title") or "").strip()
        if not title or _is_placeholder_worldbuilding_title(title):
            raise ValueError("世界观时间线缺少可识别设定标题或ID")
        dimension = _normalize_dimension(payload.get("dimension"), payload)
        entry = WorldbuildingEntry(
            project_id=chapter.project_id,
            dimension=dimension,
            title=title[:200],
            content=str(payload.get("event_description") or payload.get("content") or "")[:12000],
            sort_order=next_worldbuilding_sort_order(db, chapter.project_id, dimension),
            first_seen_chapter_id=chapter.id,
            last_updated_chapter_id=chapter.id,
        )
        db.add(entry)
        db.flush()
        ensure_worldbuilding_version(db, entry, chapter, payload)
    description = str(payload.get("event_description") or payload.get("description") or "")[:4000]
    if not description:
        raise ValueError("世界观时间线事件为空")
    event_type = str(payload.get("event_type") or "fact_change")[:50]
    sort_order = int(payload.get("sort_order") or candidate.sort_order or 0)
    preferred_id = str(payload.get("_cataloging_target_id") or "").strip()
    event = (
        db.query(WorldbuildingTimeline)
        .filter(
            WorldbuildingTimeline.id == preferred_id,
            WorldbuildingTimeline.chapter_id == chapter.id,
            WorldbuildingTimeline.entry_id == entry.id,
        )
        .first()
        if preferred_id
        else None
    )
    if not event:
        event = (
            db.query(WorldbuildingTimeline)
            .filter(
                WorldbuildingTimeline.entry_id == entry.id,
                WorldbuildingTimeline.chapter_id == chapter.id,
                WorldbuildingTimeline.event_type == event_type,
                WorldbuildingTimeline.sort_order == sort_order,
            )
            .order_by(WorldbuildingTimeline.created_at.asc())
            .first()
        )
    old = None
    if event:
        old = {
            "event_description": event.event_description,
            "event_type": event.event_type,
            "evidence": event.evidence,
            "sort_order": event.sort_order,
        }
        event.event_description = description
        event.event_type = event_type
        event.evidence = str(payload.get("evidence") or candidate.evidence or "")[:2000]
        event.sort_order = sort_order
    else:
        event = WorldbuildingTimeline(
            entry_id=entry.id,
            chapter_id=chapter.id,
            event_description=description,
            event_type=event_type,
            evidence=str(payload.get("evidence") or candidate.evidence or "")[:2000],
            sort_order=sort_order,
        )
        db.add(event)
    link_chapter_worldbuilding(db, chapter, entry, event.event_description)
    db.flush()
    return {
        "target_type": "worldbuilding_timeline",
        "target_id": event.id,
        "old_value": old,
        "new_value": payload,
        "detail": f"世界观时间线已写入: {entry.title}",
    }


def _is_placeholder_worldbuilding_title(title: str | None) -> bool:
    text = str(title or "").strip()
    return not text or text in PLACEHOLDER_WORLDBUILDING_TITLES or text.startswith("未命名")


def _chapter_can_advance_world_state(
    db: Session,
    entry: WorldbuildingEntry,
    chapter: Chapter,
) -> bool:
    if not entry.last_updated_chapter_id or entry.last_updated_chapter_id == chapter.id:
        return True
    latest = db.query(Chapter).filter(Chapter.id == entry.last_updated_chapter_id).first()
    if not latest:
        return True
    return int(chapter.sort_order or 0) >= int(latest.sort_order or 0)


def ensure_worldbuilding_version(
    db: Session,
    entry: WorldbuildingEntry,
    chapter: Chapter,
    payload: dict[str, Any],
) -> None:
    current = db.query(func.max(WorldbuildingVersion.version_number)).filter(
        WorldbuildingVersion.entry_id == entry.id
    ).scalar() or 0
    db.add(WorldbuildingVersion(
        entry_id=entry.id,
        version_number=int(current) + 1,
        snapshot_data=json.dumps(worldbuilding_snapshot(entry), ensure_ascii=False),
        change_summary=chapter_change_title(
            chapter,
            payload.get("change_summary") or payload.get("event_description") or "设定更新",
        ),
        source_chapter_id=chapter.id,
    ))


DIMENSION_ALIASES = {
    "geo": "geography",
    "location": "geography",
    "place": "geography",
    "map": "geography",
    "地理": "geography",
    "地点": "geography",
    "地图": "geography",
    "地貌": "geography",
    "区域": "geography",
    "历史": "history",
    "时间线": "history",
    "过往": "history",
    "传说": "history",
    "起源": "history",
    "history_line": "history",
    "faction": "factions",
    "organization": "factions",
    "sect": "factions",
    "势力": "factions",
    "组织": "factions",
    "宗门": "factions",
    "门派": "factions",
    "家族": "factions",
    "阵营": "factions",
    "power": "power_system",
    "magic": "power_system",
    "cultivation": "power_system",
    "power_systems": "power_system",
    "修炼体系": "power_system",
    "修炼": "power_system",
    "力量体系": "power_system",
    "功法": "power_system",
    "境界": "power_system",
    "阵法": "power_system",
    "法术": "power_system",
    "灵力": "power_system",
    "规则": "power_system",
    "种族": "races",
    "族群": "races",
    "生物": "races",
    "妖兽": "races",
    "魔族": "races",
    "race": "races",
    "species": "races",
    "习俗": "culture",
    "文化": "culture",
    "制度": "culture",
    "礼仪": "culture",
    "节日": "culture",
    "生活": "culture",
}


DIMENSION_KEYWORDS = [
    ("factions", ["宗门", "门派", "家族", "王朝", "朝廷", "组织", "势力", "阵营", "公会", "帮派"]),
    ("power_system", ["修炼", "境界", "功法", "灵气", "灵力", "法术", "术法", "阵法", "符", "血脉", "病毒", "诅咒", "封印", "规则"]),
    ("geography", ["山", "谷", "城", "村", "镇", "河", "海", "岛", "洞", "矿", "地域", "地图", "方位", "边境"]),
    ("races", ["妖兽", "灵兽", "魔族", "妖族", "血魔", "种族", "族群", "生灵"]),
    ("history", ["历史", "传说", "起源", "前代", "旧事", "纪年", "年代", "传承", "遗迹", "灭亡"]),
    ("culture", ["习俗", "节日", "礼仪", "制度", "风俗", "饮食", "服饰", "婚丧", "规矩"]),
]


def _normalize_dimension(value: Any, payload: dict[str, Any] | None = None) -> str:
    raw = str(value or "").strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in WORLD_DIMENSIONS:
        return normalized
    if raw in DIMENSION_ALIASES:
        return DIMENSION_ALIASES[raw]
    if normalized in DIMENSION_ALIASES:
        return DIMENSION_ALIASES[normalized]

    text = ""
    if payload:
        text = " ".join(
            str(payload.get(key) or "")
            for key in ["title", "name", "content", "event_description", "evidence"]
        )
    for dimension, keywords in DIMENSION_KEYWORDS:
        if any(keyword in raw or keyword in text for keyword in keywords):
            return dimension
    return "culture"
