"""Outline cataloging writes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter, OutlineNode
from ...modules.continuity.domain.outline_character_contract import outline_character_ids
from ...modules.continuity.domain.portable_identity import portable_cataloging_id
from ..story_granularity import (
    normalize_section_scene_state,
)
from .facts import record_cataloging_fact
from .links import link_outline_characters, resolve_outline_characters
from .lookups import next_outline_sort_order
from .snapshots import outline_snapshot


def apply_outline(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
    create: bool,
) -> dict[str, Any]:
    character_ids = outline_character_ids(payload)
    resolve_outline_characters(db, chapter.project_id, character_ids)
    title = payload["title"].strip()
    if not title:
        raise ValueError("大纲标题为空")
    node_type = payload["node_type"]
    scene_number = None
    if node_type == "section":
        try:
            scene_number = int(payload.get("scene_number"))
        except (TypeError, ValueError):
            scene_number = 0
        if scene_number <= 0:
            raise ValueError("场景大纲缺少有效的 scene_number，拒绝写入不稳定场景标识")
        payload["scene_number"] = scene_number
    node, parent = validate_outline_target(db, chapter, payload, create=create)
    if node_type == "volume":
        parent_id = None
    elif node_type == "chapter":
        if node is not None:
            # A chapter outline is replaced in place.  Its persisted identity,
            # parent and sort order are deterministic placement data; a new
            # model title must not move it to a fallback volume.
            parent_id = node.parent_id
        else:
            volume = (
                parent
                if parent and parent.node_type == "volume"
                else _volume_for_chapter(db, chapter)
            )
            parent_id = volume.id
    else:
        chapter_parent = _linked_chapter_outline(db, chapter)
        if not chapter_parent and parent and parent.node_type == "chapter":
            chapter_parent = parent
        if not chapter_parent:
            chapter_parent = _ensure_chapter_container(db, chapter)
        parent_id = chapter_parent.id
    old = outline_snapshot(node) if node else None
    if not node:
        client_id = payload.get("client_id")
        if client_id and db.get(OutlineNode, client_id) is not None:
            raise ValueError("新大纲 client_id 已被占用")
        node = OutlineNode(
            **({"id": client_id} if client_id else {}),
            project_id=chapter.project_id,
            parent_id=parent_id,
            node_type=node_type,
            title=title[:200],
            summary=str(payload.get("summary") or payload.get("actual_summary") or "")[:8000],
            status=str(payload.get("status") or "completed")[:20],
            source_chapter_id=chapter.id,
            actual_summary=str(
                payload.get("actual_summary") or payload.get("summary") or ""
            )[:8000],
            planned_summary=str(payload.get("planned_summary") or "")[:8000],
            cataloging_status="cataloged",
            sort_order=next_outline_sort_order(db, chapter.project_id, parent_id),
        )
        db.add(node)
        db.flush()
    else:
        if node.parent_id != parent_id:
            node.parent_id = parent_id
        if payload.get("node_type"):
            node.node_type = node_type
        if payload.get("title"):
            node.title = title[:200]
        actual_summary = str(payload.get("actual_summary") or payload.get("summary") or "")[:8000]
        if actual_summary:
            # Replace the visible outline with this saved chapter projection.
            # Keep the original plan separately for audit and later comparison.
            node.actual_summary = actual_summary
            node.summary = actual_summary
        if payload.get("planned_summary") and not node.planned_summary:
            node.planned_summary = str(payload.get("planned_summary"))[:8000]
        if payload.get("status"):
            node.status = str(payload.get("status"))[:20]
        node.source_chapter_id = node.source_chapter_id or chapter.id
        node.cataloging_status = "cataloged"

    if node.node_type == "section":
        metadata = dict(node.metadata_json or {})
        metadata.update({
            "source": "cataloging",
            "source_chapter_id": chapter.id,
            "scene_number": scene_number,
        })
        node.metadata_json = metadata

    if node.node_type == "chapter":
        # Reaching this writer means a saved chapter version has completed its
        # archive projection.  Do not leave the author-owned plan marked
        # "pending" merely because the model omitted a redundant status field.
        node.status = "completed"
        chapter.outline_node_id = node.id
    elif parent_id and not chapter.outline_node_id:
        chapter.outline_node_id = parent_id
    link_outline_characters(
        db,
        chapter.project_id,
        node,
        character_ids,
        replace=(
            node.node_type == "section"
            and node.source_chapter_id == chapter.id
            and node.cataloging_status == "cataloged"
        ),
    )
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


def _linked_chapter_outline(db: Session, chapter: Chapter) -> OutlineNode | None:
    if not chapter.outline_node_id:
        return None
    node = db.get(OutlineNode, chapter.outline_node_id)
    if node is None or node.project_id != chapter.project_id or node.node_type != "chapter":
        raise ValueError("当前章节的大纲绑定无效；请先修正章节与章级大纲的关联")
    return node


def validate_outline_target(
    db: Session, chapter: Chapter, payload: dict[str, Any], *, create: bool,
) -> tuple[OutlineNode | None, OutlineNode | None]:
    """Validate explicit IDs and stable chapter/scene projection identities."""
    node_type = payload["node_type"]
    linked = _linked_chapter_outline(db, chapter)
    identity = payload.get("id")
    node = db.get(OutlineNode, identity) if identity else None
    if identity:
        if node is None or node.project_id != chapter.project_id or node.node_type != node_type:
            raise ValueError("outline.id 不存在、不属于当前作品或节点类型不符")
        if node_type != "volume":
            if node.source_chapter_id not in {None, chapter.id}:
                raise ValueError("outline.id 已属于其他章节")
            if db.query(Chapter).filter(Chapter.outline_node_id == node.id, Chapter.id != chapter.id).first():
                raise ValueError("outline.id 已绑定其他章节")
        if node_type == "chapter" and linked is not None and node.id != linked.id:
            raise ValueError("outline.id 必须使用当前章节已绑定的大纲 ID")
        if node_type == "section":
            if not linked or node.parent_id != linked.id:
                raise ValueError("场景大纲 ID 必须属于本章大纲")
            number = (node.metadata_json or {}).get("scene_number")
            if number is not None and number != payload.get("scene_number"):
                raise ValueError("场景大纲 ID 与 scene_number 不一致")
    elif not create:
        raise ValueError("outline_update 必须提供真实 ID")
    elif node_type == "chapter":
        node = linked
    elif node_type == "section":
        node = _find_cataloged_section_by_scene_number(db, chapter, payload.get("scene_number"))

    parent_id = payload.get("parent_id")
    parent = db.get(OutlineNode, parent_id) if parent_id else None
    if parent_id:
        if parent is None or parent.project_id != chapter.project_id:
            raise ValueError("outline.parent_id 必须是当前作品真实大纲 ID，不能使用标题")
        expected_type = "volume" if node_type == "chapter" else "chapter"
        if node_type == "volume" or parent.node_type != expected_type:
            raise ValueError("outline.parent_id 的节点类型不符")
        if node_type == "chapter" and node is not None and parent.id != node.parent_id:
            raise ValueError("建档不能移动已绑定章节；parent_id 必须保留原父节点")
        if node_type == "section" and (linked is None or parent.id != linked.id):
            raise ValueError("场景大纲 parent_id 必须是本章大纲 ID")
    return node, parent


def _find_cataloged_section_by_scene_number(
    db: Session,
    chapter: Chapter,
    scene_number: Any,
) -> OutlineNode | None:
    try:
        wanted = int(scene_number)
    except (TypeError, ValueError):
        return None
    if wanted <= 0:
        return None
    rows = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == chapter.project_id,
            OutlineNode.node_type == "section",
            OutlineNode.source_chapter_id == chapter.id,
            OutlineNode.cataloging_status == "cataloged",
        )
        .order_by(OutlineNode.created_at.asc(), OutlineNode.id.asc())
        .all()
    )
    matches = []
    for row in rows:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        try:
            observed = int(metadata.get("scene_number"))
        except (TypeError, ValueError):
            continue
        if observed == wanted:
            matches.append(row)
    if len(matches) > 1:
        raise ValueError("本章存在重复场景序号，请由模型选择实际场景 ID")
    return matches[0] if matches else None


def _volume_for_chapter(db: Session, chapter: Chapter) -> OutlineNode:
    volumes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == chapter.project_id, OutlineNode.node_type == "volume")
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )
    if volumes:
        defaults = [volume for volume in volumes
                    if (volume.metadata_json or {}).get("source") == "cataloging_default_volume"]
        if len(defaults) == 1:
            return defaults[0]
        raise ValueError("新章节尚未绑定大纲，请读取大纲索引并填写所属卷的真实 parent_id")

    volume = OutlineNode(
        id=portable_cataloging_id("cataloging_default_volume", chapter.project_id),
        project_id=chapter.project_id,
        parent_id=None,
        node_type="volume",
        title="第一卷",
        summary="作品建档自动建立的默认分卷；可在大纲中重命名或调整章节范围。",
        status="in_progress",
        source_chapter_id=chapter.id,
        actual_summary="",
        planned_summary="",
        metadata_json={
            "source": "cataloging_default_volume",
            "start_chapter": 1,
        },
        cataloging_status="cataloged",
        sort_order=next_outline_sort_order(db, chapter.project_id, None),
    )
    db.add(volume)
    db.flush()
    return volume


def _ensure_chapter_container(db: Session, chapter: Chapter) -> OutlineNode:
    existing = _linked_chapter_outline(db, chapter)
    if existing:
        return existing
    volume = _volume_for_chapter(db, chapter)
    node = OutlineNode(
        project_id=chapter.project_id,
        parent_id=volume.id,
        node_type="chapter",
        title=chapter.title[:200],
        summary="",
        status="completed",
        source_chapter_id=chapter.id,
        actual_summary="",
        planned_summary="",
        cataloging_status="cataloged",
        sort_order=next_outline_sort_order(db, chapter.project_id, volume.id),
    )
    db.add(node)
    db.flush()
    chapter.outline_node_id = node.id
    return node
