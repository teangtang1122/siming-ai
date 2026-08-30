"""Version-aware reconciliation for chapter-derived cataloging data.

Cataloging candidates are an append-only audit log, but the project views they
materialize are projections of the *current* saved chapter.  This module maps a
new candidate to the target used by the previous run and retires derived rows
that disappeared from the latest successful projection.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    ChapterCharacter,
    ChapterWorldbuilding,
    CharacterTimeline,
    OutlineNode,
    WorldbuildingTimeline,
)
from .candidate_io import candidate_payload
from .links import link_outline_characters
from .lookups import find_character_by_name_or_id, find_worldbuilding_by_title_or_id

PROFILE_FAMILIES = {
    "character_create": "character_profile",
    "character_update": "character_profile",
    "worldbuilding_create": "worldbuilding",
    "worldbuilding_update": "worldbuilding",
    "outline_create": "outline",
    "outline_update": "outline",
}


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().lower(), flags=re.UNICODE)


def _positive_order(candidate: CatalogingCandidate, payload: dict[str, Any]) -> int:
    try:
        value = int(payload.get("sort_order") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else int(candidate.sort_order or 0)


def reconciliation_key(
    candidate: CatalogingCandidate, payload: dict[str, Any] | None = None
) -> str:
    payload = payload or candidate_payload(candidate)
    item_type = str(candidate.item_type or "")
    family = PROFILE_FAMILIES.get(item_type, item_type)
    if family == "outline":
        node_type = str(payload.get("node_type") or "chapter").lower()
        if node_type in {"scene", "section"}:
            scene_number = payload.get("scene_number")
            if scene_number not in (None, ""):
                return f"outline:section:scene:{scene_number}"
            title = str(payload.get("title") or candidate.target_name or "").rsplit("/", 1)[-1]
            return f"outline:section:{_normalized(title) or _positive_order(candidate, payload)}"
        return f"outline:{node_type}"
    if family == "character_profile" or item_type == "character_state_update":
        identity = (
            payload.get("name")
            or payload.get("character_name")
            or candidate.target_name
            or payload.get("id")
        )
        return f"{family}:{_normalized(identity)}"
    if item_type == "character_timeline":
        identity = (
            payload.get("name")
            or payload.get("character_name")
            or candidate.target_name
            or payload.get("id")
        )
        event_type = payload.get("event_type") or "key_event"
        return ":".join(
            (
                "character_timeline",
                _normalized(identity),
                _normalized(event_type),
                str(_positive_order(candidate, payload)),
            )
        )
    if item_type == "character_relationship":
        source = payload.get("source_name") or payload.get("character_a") or payload.get("source")
        target = payload.get("target_name") or payload.get("character_b") or payload.get("target")
        return f"character_relationship:{_normalized(source)}:{_normalized(target)}"
    if family == "worldbuilding":
        identity = payload.get("title") or candidate.target_name or payload.get("id")
        return f"worldbuilding:{_normalized(identity)}"
    if item_type == "worldbuilding_timeline":
        identity = payload.get("title") or candidate.target_name or payload.get("id")
        event_type = payload.get("event_type") or "fact_change"
        return ":".join(
            (
                "worldbuilding_timeline",
                _normalized(identity),
                _normalized(event_type),
                str(_positive_order(candidate, payload)),
            )
        )
    if item_type in {"chapter_summary", "chapter_link"}:
        return item_type
    return f"{family}:{_normalized(candidate.target_name)}:{int(candidate.sort_order or 0)}"


def _family_item_types(item_type: str) -> set[str]:
    family = PROFILE_FAMILIES.get(item_type)
    if family == "character_profile":
        return {"character_create", "character_update"}
    if family == "worldbuilding":
        return {"worldbuilding_create", "worldbuilding_update"}
    if family == "outline":
        return {"outline_create", "outline_update"}
    return {item_type}


def previous_applied_candidate(
    db: Session,
    candidate: CatalogingCandidate,
    payload: dict[str, Any],
) -> CatalogingCandidate | None:
    wanted = reconciliation_key(candidate, payload)
    rows = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_id == candidate.chapter_id,
            CatalogingCandidate.id != candidate.id,
            CatalogingCandidate.status == "applied",
            CatalogingCandidate.item_type.in_(_family_item_types(candidate.item_type)),
        )
        .order_by(CatalogingCandidate.updated_at.desc(), CatalogingCandidate.created_at.desc())
        .limit(300)
        .all()
    )
    return next((row for row in rows if reconciliation_key(row) == wanted), None)


def prepare_reconciled_payload(
    db: Session,
    candidate: CatalogingCandidate,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Prefer the prior projection target without altering the audit payload."""
    prepared = dict(payload)
    previous = previous_applied_candidate(db, candidate, prepared)
    if not previous:
        return prepared
    previous_payload = candidate_payload(previous)
    prepared["_cataloging_previous_payload"] = previous_payload
    if previous.target_id:
        if candidate.item_type in {
            "outline_create",
            "outline_update",
            "character_create",
            "character_update",
            "character_state_update",
            "worldbuilding_create",
            "worldbuilding_update",
        }:
            prepared["id"] = previous.target_id
        else:
            prepared["_cataloging_target_id"] = previous.target_id
    return prepared


def _payload(candidate: CatalogingCandidate) -> dict[str, Any]:
    try:
        value = json.loads(candidate.edited_payload or candidate.raw_payload or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _dedupe_links(
    db: Session, model: Any, chapter_id: str, target_field: str, keep_ids: set[str]
) -> None:
    rows = (
        db.query(model)
        .filter(model.chapter_id == chapter_id)
        .order_by(model.created_at.asc())
        .all()
    )
    seen: set[str] = set()
    for row in rows:
        target_id = str(getattr(row, target_field))
        if target_id not in keep_ids or target_id in seen:
            db.delete(row)
        else:
            seen.add(target_id)


def _projection_link_targets(
    db: Session,
    run: CatalogingChapterRun,
    applied: list[CatalogingCandidate],
) -> tuple[set[str], set[str]]:
    """Collect the exact chapter-level links represented by the current run."""
    character_ids: set[str] = set()
    world_ids: set[str] = set()
    for row in applied:
        payload = _payload(row)
        if row.item_type in {
            "character_create",
            "character_update",
            "character_state_update",
        } and row.target_id:
            character_ids.add(str(row.target_id))
        if row.item_type == "character_timeline" and row.target_id:
            event = db.get(CharacterTimeline, row.target_id)
            if event:
                character_ids.add(str(event.character_id))
        if row.item_type == "character_relationship":
            for name in (
                payload.get("source_name") or payload.get("character_a"),
                payload.get("target_name") or payload.get("character_b"),
            ):
                character = find_character_by_name_or_id(db, run.project_id, name)
                if character:
                    character_ids.add(str(character.id))
        if row.item_type in {"worldbuilding_create", "worldbuilding_update"} and row.target_id:
            world_ids.add(str(row.target_id))
        if row.item_type == "worldbuilding_timeline" and row.target_id:
            event = db.get(WorldbuildingTimeline, row.target_id)
            if event:
                world_ids.add(str(event.entry_id))
        if row.item_type == "chapter_link":
            for name in payload.get("character_names") or []:
                character = find_character_by_name_or_id(db, run.project_id, name)
                if character:
                    character_ids.add(str(character.id))
            for title in payload.get("worldbuilding_titles") or []:
                entry = find_worldbuilding_by_title_or_id(db, run.project_id, title)
                if entry:
                    world_ids.add(str(entry.id))
    return character_ids, world_ids


def reconcile_successful_run(db: Session, run: CatalogingChapterRun) -> dict[str, int]:
    """Make replaceable derived collections equal the latest successful run."""
    current = (
        db.query(CatalogingCandidate).filter(CatalogingCandidate.chapter_run_id == run.id).all()
    )
    if any(row.status in {"apply_failed", "applying", "pending"} for row in current):
        return {"skipped": 1}
    applied = [row for row in current if row.status == "applied"]
    target_ids = {str(row.target_id) for row in applied if row.target_id}

    removed_sections = 0
    section_ids = {
        str(row.target_id)
        for row in applied
        if row.target_id
        and row.item_type in {"outline_create", "outline_update"}
        and str(_payload(row).get("node_type") or "chapter").lower() in {"section", "scene"}
    }
    stale_sections = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == run.project_id,
            OutlineNode.source_chapter_id == run.chapter_id,
            OutlineNode.node_type == "section",
            OutlineNode.cataloging_status == "cataloged",
        )
        .all()
    )
    for node in stale_sections:
        if str(node.id) not in section_ids:
            db.delete(node)
            removed_sections += 1

    # Outline candidates are applied before newly discovered characters.  Now
    # that the complete run has been materialized, reconcile the scene links a
    # second time so retained scenes neither miss new characters nor keep
    # characters removed by the revised chapter.
    for row in applied:
        if row.item_type not in {"outline_create", "outline_update"} or not row.target_id:
            continue
        node = db.get(OutlineNode, row.target_id)
        if not node:
            continue
        payload = _payload(row)
        link_outline_characters(
            db,
            run.project_id,
            node,
            payload.get("related_characters", []),
            replace=(
                node.node_type == "section"
                and node.source_chapter_id == run.chapter_id
                and node.cataloging_status == "cataloged"
            ),
        )

    character_timeline_ids = {
        str(row.target_id)
        for row in applied
        if row.item_type == "character_timeline" and row.target_id
    }
    removed_character_timeline = (
        db.query(CharacterTimeline)
        .filter(
            CharacterTimeline.chapter_id == run.chapter_id,
            CharacterTimeline.id.notin_(character_timeline_ids) if character_timeline_ids else True,
        )
        .delete(synchronize_session=False)
    )

    world_timeline_ids = {
        str(row.target_id)
        for row in applied
        if row.item_type == "worldbuilding_timeline" and row.target_id
    }
    removed_world_timeline = (
        db.query(WorldbuildingTimeline)
        .filter(
            WorldbuildingTimeline.chapter_id == run.chapter_id,
            WorldbuildingTimeline.id.notin_(world_timeline_ids) if world_timeline_ids else True,
        )
        .delete(synchronize_session=False)
    )

    character_ids, world_ids = _projection_link_targets(db, run, applied)

    _dedupe_links(db, ChapterCharacter, run.chapter_id, "character_id", character_ids)
    _dedupe_links(
        db,
        ChapterWorldbuilding,
        run.chapter_id,
        "worldbuilding_entry_id",
        world_ids,
    )

    # Facts are cataloging-owned snapshots. A fact omitted by the latest
    # successful version must no longer remain active.
    from ...database.models import CatalogingFact

    retired_facts = (
        db.query(CatalogingFact)
        .filter(
            CatalogingFact.chapter_id == run.chapter_id,
            CatalogingFact.chapter_run_id != run.id,
            CatalogingFact.status == "active",
        )
        .update({"status": "superseded"}, synchronize_session=False)
    )
    db.flush()
    return {
        "removed_sections": removed_sections,
        "removed_character_timeline": int(removed_character_timeline or 0),
        "removed_world_timeline": int(removed_world_timeline or 0),
        "retired_facts": int(retired_facts or 0),
        "active_targets": len(target_ids),
    }
