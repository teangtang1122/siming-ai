"""Outline cataloging writes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter, OutlineNode
from ..story_granularity import normalize_section_scene_state
from .facts import record_cataloging_fact
from .links import link_outline_characters
from .lookups import find_outline_by_title_or_id, next_outline_sort_order
from .merge import merge_text
from .snapshots import outline_snapshot


def apply_outline(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
    create: bool,
) -> dict[str, Any]:
    title = str(payload.get("title") or payload.get("target_name") or chapter.title).strip()
    if not title:
        raise ValueError("大纲标题为空")
    node_type = str(payload.get("node_type") or "chapter")[:20]
    if node_type == "scene":
        node_type = "section"
    parent_id = payload.get("parent_id")
    parent_title = payload.get("parent_title")
    if not parent_id and parent_title:
        parent = find_outline_by_title_or_id(db, chapter.project_id, parent_title)
        parent_id = parent.id if parent else None
    node = _find_exact_outline(db, chapter.project_id, payload.get("id") or title) if create else find_outline_by_title_or_id(
        db,
        chapter.project_id,
        payload.get("id") or title,
    )
    old = outline_snapshot(node) if node else None
    if not node:
        node = OutlineNode(
            project_id=chapter.project_id,
            parent_id=parent_id,
            node_type=node_type,
            title=title[:200],
            summary=str(payload.get("summary") or payload.get("actual_summary") or "")[:8000],
            status=str(payload.get("status") or "completed")[:20],
            source_chapter_id=chapter.id,
            actual_summary=str(payload.get("actual_summary") or payload.get("summary") or "")[:8000],
            planned_summary=str(payload.get("planned_summary") or "")[:8000],
            cataloging_status="cataloged",
            sort_order=next_outline_sort_order(db, chapter.project_id, parent_id),
        )
        db.add(node)
        db.flush()
    else:
        if parent_id and node.parent_id != parent_id:
            node.parent_id = parent_id
        if payload.get("node_type"):
            node.node_type = node_type
        if payload.get("title"):
            node.title = title[:200]
        if payload.get("summary") or payload.get("actual_summary"):
            node.summary = merge_text(node.summary, payload.get("summary") or payload.get("actual_summary"), chapter, limit=8000)
            node.actual_summary = merge_text(node.actual_summary, payload.get("actual_summary") or payload.get("summary"), chapter, limit=8000)
        if payload.get("planned_summary"):
            node.planned_summary = merge_text(node.planned_summary, payload.get("planned_summary"), chapter, limit=8000)
        if payload.get("status"):
            node.status = str(payload.get("status"))[:20]
        node.source_chapter_id = node.source_chapter_id or chapter.id
        node.cataloging_status = "cataloged"

    if node.node_type == "chapter":
        chapter.outline_node_id = node.id
    elif parent_id and not chapter.outline_node_id:
        chapter.outline_node_id = parent_id
    link_outline_characters(db, chapter.project_id, node, payload.get("related_characters"))
    scene_state = normalize_section_scene_state(payload)
    fact = None
    if scene_state:
        scene_state.setdefault("outline_node_id", node.id)
        scene_state.setdefault("title", node.title)
        scene_state.setdefault("chapter_id", chapter.id)
        scene_state.setdefault("chapter_title", chapter.title)
        fact = record_cataloging_fact(
            db,
            candidate,
            chapter,
            fact_type="section_scene_state",
            payload=scene_state,
            identity_keys=("outline_node_id", "title"),
        )
    return {
        "target_type": "outline_node",
        "target_id": node.id,
        "old_value": old,
        "new_value": {**outline_snapshot(node), "scene_fact_id": fact.id if fact else None},
        "detail": "大纲节点已写入",
    }


def _find_exact_outline(db: Session, project_id: str, value: Any) -> OutlineNode | None:
    text = str(value or "").strip()
    if not text:
        return None
    return (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .filter((OutlineNode.id == text) | (OutlineNode.title == text))
        .order_by(OutlineNode.updated_at.desc())
        .first()
    )
