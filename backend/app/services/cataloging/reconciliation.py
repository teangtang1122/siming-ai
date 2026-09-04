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

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    ChapterCharacter,
    ChapterWorldbuilding,
    CharacterTimeline,
    OutlineNode,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from ...database.query_filters import (
    current_worldbuilding_clause,
    is_current_worldbuilding_status,
)
from .candidate_io import candidate_payload
from .links import link_outline_characters
from .lookups import find_character_by_name_or_id, find_worldbuilding_by_title_or_id
from .snapshots import worldbuilding_snapshot

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
            CatalogingCandidate.chapter_run_id != candidate.chapter_run_id,
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
    prepared = {key: value for key, value in payload.items() if not key.startswith("_cataloging_")}
    previous = previous_applied_candidate(db, candidate, prepared)
    if (
        previous
        and candidate.item_type in {"character_update", "character_state_update"}
        and prepared.get("id")
        and previous.target_id != prepared["id"]
    ):
        previous = None
    if not previous:
        return prepared
    previous_payload = candidate_payload(previous)
    if previous.target_id:
        if candidate.item_type in {"worldbuilding_create", "worldbuilding_update"}:
            previous_entry = db.get(WorldbuildingEntry, previous.target_id)
            if previous_entry is None or not is_current_worldbuilding_status(
                previous_entry.status
            ):
                # An author may explicitly retire a chapter-derived card after
                # reviewing an earlier catalog run.  A later version must not
                # silently undo that decision merely because the model emits
                # the same reconciliation key again.
                prepared["_cataloging_suppressed_target_id"] = previous.target_id
                prepared["_cataloging_suppressed_target_status"] = (
                    previous_entry.status if previous_entry is not None else "deleted"
                )
                return prepared
        prepared["_cataloging_previous_payload"] = previous_payload
        if candidate.item_type == "character_update":
            previous_apply = (
                db.query(CatalogingApplyLog)
                .filter(
                    CatalogingApplyLog.candidate_id == previous.id,
                    CatalogingApplyLog.target_id == previous.target_id,
                )
                .order_by(CatalogingApplyLog.applied_at.desc())
                .first()
            )
            if previous_apply:
                previous_old = _json_object(previous_apply.old_value)
                previous_new = _json_object(previous_apply.new_value)
                if previous_old is not None and previous_new is not None:
                    prepared["_cataloging_previous_old_snapshot"] = previous_old
                    prepared["_cataloging_previous_new_snapshot"] = previous_new
        if candidate.item_type in {
            "outline_create",
            "outline_update",
            "worldbuilding_create",
            "worldbuilding_update",
        }:
            prepared["id"] = previous.target_id
        elif candidate.item_type not in {
            "character_create",
            "character_update",
            "character_state_update",
        }:
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
        if (
            row.item_type in {"worldbuilding_create", "worldbuilding_update"}
            and row.target_id
            and row.target_type != "worldbuilding_suppressed"
        ):
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


def _direct_worldbuilding_target_ids(
    db: Session,
    applied: list[CatalogingCandidate],
) -> set[str]:
    """Return world entries explicitly materialized by this projection.

    ``chapter_link`` is intentionally excluded.  A link only says that an
    existing card is relevant to the chapter; it cannot keep a chapter-owned
    card from an older saved version authoritative after the new version omits
    the corresponding create/update/timeline candidate.
    """

    target_ids = {
        str(row.target_id)
        for row in applied
        if row.item_type in {"worldbuilding_create", "worldbuilding_update"}
        and row.target_id
        and row.target_type != "worldbuilding_suppressed"
    }
    for row in applied:
        if row.item_type != "worldbuilding_timeline" or not row.target_id:
            continue
        event = db.get(WorldbuildingTimeline, row.target_id)
        if event:
            target_ids.add(str(event.entry_id))
    return target_ids


def _append_review_warning(run: CatalogingChapterRun, warning: str) -> None:
    text = str(warning or "").strip("； ")
    if not text or text in str(run.review_warning or ""):
        return
    run.review_warning = "；".join(
        value
        for value in (str(run.review_warning or "").strip("； "), text)
        if value
    )[:4000]


def _json_object(value: str | None) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value or "null")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _restore_worldbuilding_snapshot(
    entry: WorldbuildingEntry,
    snapshot: dict[str, Any],
) -> None:
    if str(snapshot.get("id") or entry.id) != str(entry.id):
        raise ValueError("世界观回退快照目标不一致")
    for field in ("dimension", "title", "content", "status", "confidence"):
        if field in snapshot:
            setattr(entry, field, snapshot[field])


def _restore_omitted_worldbuilding_updates(
    db: Session,
    run: CatalogingChapterRun,
    direct_target_ids: set[str],
) -> tuple[int, int]:
    """Undo stale contributions from older versions when the audit proves safety.

    Existing shared cards may have been updated by an older saved version of
    this chapter.  If the latest projection omits that direct target, replay the
    apply log backwards only while the live card exactly equals the logged
    post-apply snapshot.  Any author or later-chapter mutation changes that
    snapshot and therefore stops the rollback instead of being overwritten.
    """

    history = (
        db.query(CatalogingApplyLog, CatalogingCandidate, CatalogingChapterRun)
        .join(CatalogingCandidate, CatalogingCandidate.id == CatalogingApplyLog.candidate_id)
        .join(
            CatalogingChapterRun,
            CatalogingChapterRun.id == CatalogingApplyLog.chapter_run_id,
        )
        .filter(
            CatalogingCandidate.chapter_id == run.chapter_id,
            CatalogingCandidate.chapter_run_id != run.id,
            CatalogingCandidate.status == "applied",
            CatalogingCandidate.item_type.in_(
                {"worldbuilding_create", "worldbuilding_update"}
            ),
            CatalogingApplyLog.target_type == "worldbuilding",
            CatalogingApplyLog.target_id.isnot(None),
            CatalogingChapterRun.created_at < run.created_at,
        )
        .order_by(CatalogingApplyLog.applied_at.desc())
        .all()
    )
    by_target: dict[str, list[CatalogingApplyLog]] = {}
    for apply_log, _candidate, _previous_run in history:
        target_id = str(apply_log.target_id or "")
        if target_id and target_id not in direct_target_ids:
            by_target.setdefault(target_id, []).append(apply_log)

    restored_titles: list[str] = []
    conflict_titles: list[str] = []
    from ..rag.indexer import mark_dirty

    for target_id, apply_logs in by_target.items():
        entry = db.get(WorldbuildingEntry, target_id)
        if (
            entry is None
            or entry.project_id != run.project_id
            or entry.first_seen_chapter_id == run.chapter_id
            or not is_current_worldbuilding_status(entry.status)
        ):
            continue

        restored = False
        conflicted = False
        current_snapshot = worldbuilding_snapshot(entry)
        for apply_log in apply_logs:
            new_snapshot = _json_object(apply_log.new_value)
            old_snapshot = _json_object(apply_log.old_value)
            if not new_snapshot or not old_snapshot or current_snapshot != new_snapshot:
                conflicted = True
                break
            _restore_worldbuilding_snapshot(entry, old_snapshot)
            current_snapshot = worldbuilding_snapshot(entry)
            restored = True

        if restored:
            from .worldbuilding_ops import ensure_worldbuilding_version

            ensure_worldbuilding_version(
                db,
                entry,
                run.chapter,
                {"change_summary": "新版重建档恢复旧版本写入前的权威快照"},
            )
            mark_dirty(db, run.project_id, "worldbuilding", entry.id)
            restored_titles.append(entry.title)
        if conflicted:
            conflict_titles.append(entry.title)

    if restored_titles:
        _append_review_warning(
            run,
            "新版未再直接建档的共享世界观已恢复旧版本写入前快照："
            + "、".join(sorted(set(restored_titles))),
        )
    if conflict_titles:
        _append_review_warning(
            run,
            "以下旧版世界观写入之后已有作者或其他章节修改，系统未自动覆盖，请人工核对："
            + "、".join(sorted(set(conflict_titles))),
        )
    return len(set(restored_titles)), len(set(conflict_titles))


def reconcile_successful_run(db: Session, run: CatalogingChapterRun) -> dict[str, int]:
    """Make replaceable derived collections equal the latest successful run."""
    current = (
        db.query(CatalogingCandidate).filter(CatalogingCandidate.chapter_run_id == run.id).all()
    )
    if any(row.status in {"apply_failed", "applying", "pending"} for row in current):
        return {"skipped": 1}
    applied = [row for row in current if row.status == "applied"]
    target_ids = {str(row.target_id) for row in applied if row.target_id}
    direct_world_ids = _direct_worldbuilding_target_ids(db, applied)

    removed_sections = 0
    section_ids = {
        str(row.target_id)
        for row in applied
        if row.target_id
        and row.item_type in {"outline_create", "outline_update"}
        and str(_payload(row).get("node_type") or "chapter").lower() in {"section", "scene"}
    }
    chapter_outline_ids = {
        str(row.target_id)
        for row in applied
        if row.target_id
        and row.item_type in {"outline_create", "outline_update"}
        and str(_payload(row).get("node_type") or "chapter").lower() == "chapter"
    }
    stale_sections: list[OutlineNode] = []
    if chapter_outline_ids:
        # The latest successful chapter projection replaces the whole scene
        # collection.  Include initial planning scenes under the stable chapter
        # slot as well as older catalog-owned scenes, without title matching.
        stale_sections = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == run.project_id,
                OutlineNode.node_type == "section",
                or_(
                    OutlineNode.parent_id.in_(chapter_outline_ids),
                    OutlineNode.source_chapter_id == run.chapter_id,
                ),
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

    # A chapter-derived world card belongs to the latest saved projection of
    # that chapter.  If the revised projection omits it and no other chapter
    # uses it, retire the card instead of leaving stale facts available to
    # writing context or retrieval. A chapter_link alone is not a direct
    # projection and cannot rescue an older chapter-owned card. Shared cards
    # remain active.
    db.flush()
    retired_worldbuilding_entries = 0
    retired_worldbuilding_titles: list[str] = []
    chapter_owned_entries = (
        db.query(WorldbuildingEntry)
        .filter(
            WorldbuildingEntry.project_id == run.project_id,
            WorldbuildingEntry.first_seen_chapter_id == run.chapter_id,
            current_worldbuilding_clause(WorldbuildingEntry.status),
        )
        .all()
    )
    from ..rag.indexer import delete_source_index

    for entry in chapter_owned_entries:
        if str(entry.id) in direct_world_ids:
            continue
        shared_link = (
            db.query(ChapterWorldbuilding.id)
            .filter(
                ChapterWorldbuilding.worldbuilding_entry_id == entry.id,
                ChapterWorldbuilding.chapter_id != run.chapter_id,
            )
            .first()
        )
        if shared_link:
            continue
        entry.status = "superseded"
        world_ids.discard(str(entry.id))
        delete_source_index(db, run.project_id, "worldbuilding", entry.id)
        retired_worldbuilding_entries += 1
        retired_worldbuilding_titles.append(entry.title)

    if retired_worldbuilding_titles:
        _append_review_warning(
            run,
            "新版未再直接建档的章节自有世界观已停用，旧 chapter_link 不会继续激活它们："
            + "、".join(sorted(set(retired_worldbuilding_titles))),
        )

    restored_worldbuilding_entries, worldbuilding_restore_conflicts = (
        _restore_omitted_worldbuilding_updates(db, run, direct_world_ids)
    )

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
        "retired_worldbuilding_entries": retired_worldbuilding_entries,
        "restored_worldbuilding_entries": restored_worldbuilding_entries,
        "worldbuilding_restore_conflicts": worldbuilding_restore_conflicts,
        "active_targets": len(target_ids),
    }
