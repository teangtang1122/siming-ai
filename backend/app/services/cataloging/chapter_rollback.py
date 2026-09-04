"""Orchestrate chapter-derived cataloging rollback.

Removing or semantically rewriting a chapter invalidates the author-facing
cataloging projection from that chapter onward.  This module owns the rollback
boundary and delegates entity restoration and projection cleanup to focused
helpers.

A process-wide ``Session.before_flush`` listener covers every ORM chapter
removal surface.  Runtime workers are stopped only after the owning database
transaction commits, so a failed delete never causes an irreversible process
side effect.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.modules.continuity.infrastructure.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CharacterTimeline,
    WorldbuildingTimeline,
)
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterCharacter,
    ChapterWorldbuilding,
    CharacterRelationship,
    Project,
)

from .chapter_rollback_common import json_value, ordered_project_chapters
from .chapter_rollback_entities import (
    rollback_apply_logs,
    rollback_legacy_character_changes,
)
from .chapter_rollback_projection import (
    cleanup_chapter_owned_rows,
    clear_rag_projection,
    invalidate_cataloging_audit,
    refresh_character_provenance,
    refresh_worldbuilding_provenance,
    restore_ledger_projection,
    rollback_governance,
)

_LISTENER_INSTALLED = False
_DELETE_GUARD = "siming.chapter_cataloging_delete_rollback"
_DELETE_RESULTS = "siming.chapter_cataloging_delete_results"
_RUNTIME_CANCELLATIONS = "siming.chapter_cataloging_runtime_cancellations"


def chapter_suffix_ids(db: Session, project_id: str, chapter_id: str) -> list[str]:
    """Return one chapter and every later chapter in canonical reading order."""

    chapters = ordered_project_chapters(db, project_id)
    for index, chapter in enumerate(chapters):
        if chapter.id == chapter_id:
            return [item.id for item in chapters[index:]]
    return []


def cataloging_required_suffix_ids(
    db: Session,
    project_id: str,
    chapter_id: str,
) -> list[str]:
    """Return the still-dirty part of a chapter suffix without reordering it."""

    suffix = chapter_suffix_ids(db, project_id, chapter_id)
    if not suffix:
        return []
    required = {
        row.id
        for row in db.query(Chapter.id)
        .filter(
            Chapter.project_id == project_id,
            Chapter.id.in_(suffix),
            Chapter.cataloging_required.is_(True),
        )
        .all()
    }
    return [item for item in suffix if item in required]


def _result(
    chapter_id: str,
    affected: list[Chapter],
    recatalog: list[Chapter],
) -> dict[str, Any]:
    return {
        "trigger_chapter_id": chapter_id,
        "affected_chapter_ids": [item.id for item in affected],
        "recatalog_required_chapter_ids": [item.id for item in recatalog],
        "rolled_back_apply_logs": 0,
        "invalidated_candidates": 0,
        "invalidated_runs": 0,
        "cancelled_jobs": 0,
        "cancelled_job_ids": [],
        "deleted_characters": 0,
        "deleted_character_ids": [],
        "restored_characters": 0,
        "deleted_worldbuilding": 0,
        "deleted_worldbuilding_ids": [],
        "restored_worldbuilding": 0,
        "deleted_relationships": 0,
        "restored_relationships": 0,
        "restored_timeline_rows": 0,
        "deleted_outline_nodes": 0,
        "restored_outline_nodes": 0,
        "deleted_governance_items": 0,
        "stale_governance_items": 0,
        "stale_governance_reviews": 0,
        "governance_invalidated_count": 0,
        "legacy_character_changes_reverted": 0,
        "ledger_keys_restored": 0,
        "rag_documents_invalidated": 0,
        "removed_rows": {},
        "preserved_entities": [],
        "warnings": [],
    }


def _application_rows(
    db: Session,
    project_id: str,
    affected_ids: set[str],
) -> list[tuple[CatalogingApplyLog, CatalogingCandidate]]:
    return (
        db.query(CatalogingApplyLog, CatalogingCandidate)
        .join(
            CatalogingCandidate,
            CatalogingCandidate.id == CatalogingApplyLog.candidate_id,
        )
        .filter(
            CatalogingCandidate.project_id == project_id,
            CatalogingCandidate.chapter_id.in_(affected_ids),
        )
        .all()
    )


def _merge_character_ids_from_snapshot(
    destination: set[str],
    value: str | None,
) -> None:
    payload = json_value(value)
    if not isinstance(payload, dict):
        return
    for key in ("primary", "secondary"):
        item = payload.get(key)
        if isinstance(item, dict) and item.get("id"):
            destination.add(str(item["id"]))


def _affected_projection_ids(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    affected: list[Chapter],
    application_rows: list[tuple[CatalogingApplyLog, CatalogingCandidate]],
) -> tuple[set[str], set[str], set[str]]:
    character_ids = {
        str(value)
        for (value,) in db.query(ChapterCharacter.character_id)
        .filter(ChapterCharacter.chapter_id.in_(affected_ids))
        .all()
    }
    world_ids = {
        str(value)
        for (value,) in db.query(ChapterWorldbuilding.worldbuilding_entry_id)
        .filter(ChapterWorldbuilding.chapter_id.in_(affected_ids))
        .all()
    }
    outline_ids = {
        str(item.outline_node_id)
        for item in affected
        if item.outline_node_id
    }

    for log, candidate in application_rows:
        target_type = str(log.target_type or candidate.target_type or "")
        target_id = str(log.target_id or candidate.target_id or "")
        if target_type == "character" and target_id:
            character_ids.add(target_id)
            _merge_character_ids_from_snapshot(character_ids, log.old_value)
            _merge_character_ids_from_snapshot(character_ids, log.new_value)
        elif target_type == "worldbuilding" and target_id:
            world_ids.add(target_id)
        elif target_type == "outline_node" and target_id:
            outline_ids.add(target_id)
        elif target_type == "character_relationship" and target_id:
            relationship = db.get(CharacterRelationship, target_id)
            if relationship:
                character_ids.update(
                    {
                        relationship.character_a_id,
                        relationship.character_b_id,
                    }
                )
        elif target_type == "character_timeline" and target_id:
            timeline = db.get(CharacterTimeline, target_id)
            if timeline:
                character_ids.add(timeline.character_id)
        elif target_type == "worldbuilding_timeline" and target_id:
            timeline = db.get(WorldbuildingTimeline, target_id)
            if timeline:
                world_ids.add(timeline.entry_id)
    return character_ids, world_ids, outline_ids


def rollback_cataloging_from_chapter(
    db: Session,
    project_id: str,
    chapter_id: str,
    *,
    reason: str,
    deleting_chapter: bool = False,
    deleted_chapter_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Restore the projection to immediately before a chapter was cataloged.

    The trigger chapter and all later chapters form one invalidation suffix.
    Semantic edits keep every chapter and mark the whole suffix dirty.  Deletion
    keeps later prose but excludes all chapters being deleted from the rebuild
    queue.
    """

    ordered = ordered_project_chapters(db, project_id)
    trigger_index = next(
        (index for index, item in enumerate(ordered) if item.id == chapter_id),
        None,
    )
    if trigger_index is None:
        return {
            "affected_chapter_ids": [],
            "recatalog_required_chapter_ids": [],
            "governance_invalidated_count": 0,
            "warnings": [],
        }

    affected = ordered[trigger_index:]
    affected_ids = {item.id for item in affected}
    deleted_ids = set(deleted_chapter_ids or ())
    if deleting_chapter:
        deleted_ids.add(chapter_id)
    recatalog = [item for item in affected if item.id not in deleted_ids]
    preserved_outline_ids = {
        str(item.outline_node_id)
        for item in recatalog
        if item.outline_node_id
    }
    result = _result(chapter_id, affected, recatalog)

    application_rows = _application_rows(db, project_id, affected_ids)
    character_ids, world_ids, outline_ids = _affected_projection_ids(
        db,
        project_id,
        affected_ids,
        affected,
        application_rows,
    )

    rollback_apply_logs(
        db,
        project_id,
        affected_ids,
        outline_ids,
        preserved_outline_ids,
        result,
    )
    rollback_legacy_character_changes(db, affected_ids, result)
    cleanup_chapter_owned_rows(
        db,
        project_id,
        affected_ids,
        outline_ids,
        preserved_outline_ids,
        deleted_ids,
        result,
    )

    deleted_character_ids = set(result["deleted_character_ids"])
    deleted_world_ids = set(result["deleted_worldbuilding_ids"])
    rollback_governance(
        db,
        project_id,
        affected_ids,
        deleted_ids,
        deleted_character_ids,
        reason,
        result,
    )
    result["governance_invalidated_count"] = (
        result["deleted_governance_items"]
        + result["stale_governance_items"]
        + result["stale_governance_reviews"]
        + int(result["removed_rows"].get("chapter_governance_reviews") or 0)
    )
    result["ledger_keys_restored"] = restore_ledger_projection(
        db,
        project_id,
        affected_ids,
    )
    refresh_character_provenance(
        db,
        project_id,
        character_ids - deleted_character_ids,
    )
    refresh_worldbuilding_provenance(
        db,
        project_id,
        world_ids - deleted_world_ids,
    )
    invalidate_cataloging_audit(
        db,
        project_id,
        affected_ids,
        reason,
        result,
    )
    result["rag_documents_invalidated"] = clear_rag_projection(
        db,
        project_id,
        affected_ids,
        character_ids,
        world_ids,
        outline_ids,
    )

    for chapter in recatalog:
        chapter.cataloging_required = bool((chapter.content or "").strip())

    cancellations = db.info.setdefault(_RUNTIME_CANCELLATIONS, set())
    if isinstance(cancellations, set):
        cancellations.update(result.get("cancelled_job_ids") or [])
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["preserved_entities"] = list(
        dict.fromkeys(result["preserved_entities"])
    )
    return result


def pop_delete_rollback_result(
    db: Session,
    chapter_id: str,
) -> dict[str, Any] | None:
    results = db.info.get(_DELETE_RESULTS)
    if not isinstance(results, dict):
        return None
    value = results.pop(chapter_id, None)
    if not results:
        db.info.pop(_DELETE_RESULTS, None)
    return value if isinstance(value, dict) else None


def _before_flush_chapter_delete(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    if session.info.get(_DELETE_GUARD):
        return
    deleted_projects = {
        row.id for row in session.deleted if isinstance(row, Project)
    }
    chapters = [
        row
        for row in session.deleted
        if isinstance(row, Chapter) and row.project_id not in deleted_projects
    ]
    if not chapters:
        return

    session.info[_DELETE_GUARD] = True
    try:
        grouped: dict[str, list[Chapter]] = defaultdict(list)
        for chapter in chapters:
            grouped[chapter.project_id].append(chapter)
        for project_id, deleted in grouped.items():
            order = {
                row.id: index
                for index, row in enumerate(
                    ordered_project_chapters(session, project_id)
                )
            }
            trigger = min(
                deleted,
                key=lambda row: order.get(row.id, 1_000_000_000),
            )
            deleted_ids = {row.id for row in deleted}
            result = rollback_cataloging_from_chapter(
                session,
                project_id,
                trigger.id,
                reason=(
                    f"《{trigger.title}》已删除；"
                    "该章及后续章节的旧建档投影已回退"
                ),
                deleting_chapter=True,
                deleted_chapter_ids=deleted_ids,
            )
            results = session.info.setdefault(_DELETE_RESULTS, {})
            if isinstance(results, dict):
                for chapter in deleted:
                    results[chapter.id] = result
    finally:
        session.info.pop(_DELETE_GUARD, None)


def _after_commit_cancel_runtimes(session: Session) -> None:
    values = session.info.pop(_RUNTIME_CANCELLATIONS, set())
    if not isinstance(values, set) or not values:
        return
    from .launcher import cancel_cataloging_runtime

    cancel_cataloging_runtime(sorted(str(item) for item in values))


def _after_rollback_clear_runtimes(session: Session) -> None:
    session.info.pop(_RUNTIME_CANCELLATIONS, None)
    session.info.pop(_DELETE_RESULTS, None)


def install_chapter_delete_rollback_listener() -> None:
    """Install the process-wide chapter deletion and runtime invariants."""

    global _LISTENER_INSTALLED
    if _LISTENER_INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush_chapter_delete, insert=True)
    event.listen(Session, "after_commit", _after_commit_cancel_runtimes, insert=True)
    event.listen(Session, "after_rollback", _after_rollback_clear_runtimes, insert=True)
    _LISTENER_INSTALLED = True


install_chapter_delete_rollback_listener()


__all__ = [
    "cataloging_required_suffix_ids",
    "chapter_suffix_ids",
    "install_chapter_delete_rollback_listener",
    "pop_delete_rollback_result",
    "rollback_cataloging_from_chapter",
]
