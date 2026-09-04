"""Clean and rebuild projections invalidated by a chapter boundary change."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.modules.assistant.infrastructure.models import RagChunk, RagDocument
from app.modules.continuity.infrastructure.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    CausalEdge,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSummary,
    CharacterChangeLog,
    CharacterNarrativeState,
    CharacterTimeline,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
    NarrativeGovernanceEvent,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterWorldbuilding,
    Character,
    CharacterAlias,
    CharacterVersion,
    OutlineNode,
    OutlineNodeCharacter,
    WorldbuildingEntry,
)

from .chapter_rollback_common import (
    delete_rows,
    json_value,
    ordered_project_chapters,
    reset_chapter_outline_projection,
)


def _catalog_node_depth(node: OutlineNode) -> int:
    depth = 0
    current = node.parent
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        depth += 1
        current = current.parent
    return depth


def _catalog_node_safe_to_delete(
    db: Session,
    node: OutlineNode,
    affected_ids: set[str],
) -> bool:
    if node.source_chapter_id not in affected_ids:
        return False
    if node.cataloging_status != "cataloged":
        return False
    if (
        db.query(Chapter)
        .filter(
            Chapter.outline_node_id == node.id,
            Chapter.id.notin_(affected_ids),
        )
        .first()
    ):
        return False
    return all(
        child.source_chapter_id in affected_ids
        and child.cataloging_status == "cataloged"
        for child in node.children
    )


def _invalidate_chapter_reviews(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    deleted_chapter_ids: set[str],
    result: dict[str, Any],
) -> None:
    reviews = (
        db.query(ChapterGovernanceReview)
        .filter(
            ChapterGovernanceReview.project_id == project_id,
            ChapterGovernanceReview.chapter_id.in_(affected_ids),
        )
        .all()
    )
    deleted = 0
    stale = 0
    for review in reviews:
        if review.chapter_id in deleted_chapter_ids:
            db.delete(review)
            deleted += 1
            continue
        if review.status != "stale":
            review.status = "stale"
            review.reviewed_at = None
            review.updated_at = datetime.utcnow()
            stale += 1
    result["removed_rows"]["chapter_governance_reviews"] = deleted
    result["stale_governance_reviews"] += stale


def cleanup_chapter_owned_rows(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    preserved_outline_ids: set[str],
    deleted_chapter_ids: set[str],
    result: dict[str, Any],
) -> None:
    for key, model in (
        ("chapter_summaries", ChapterSummary),
        ("chapter_character_links", ChapterCharacter),
        ("chapter_worldbuilding_links", ChapterWorldbuilding),
        ("character_change_logs", CharacterChangeLog),
        ("character_timeline_rows", CharacterTimeline),
        ("worldbuilding_timeline_rows", WorldbuildingTimeline),
        ("character_narrative_states", CharacterNarrativeState),
        ("chapter_quality_metrics", ChapterQualityMetric),
    ):
        column = model.chapter_id
        rows = db.query(model).filter(column.in_(affected_ids)).all()
        result["removed_rows"][key] = delete_rows(rows)

    _invalidate_chapter_reviews(
        db,
        project_id,
        affected_ids,
        deleted_chapter_ids,
        result,
    )

    snapshot_ids = {
        row.id
        for row in db.query(ChapterSnapshot.id)
        .filter(ChapterSnapshot.chapter_id.in_(affected_ids))
        .all()
    }
    checkpoint_query = db.query(NarrativeCheckpoint).filter(
        NarrativeCheckpoint.project_id == project_id
    )
    checkpoint_query = checkpoint_query.filter(
        or_(
            NarrativeCheckpoint.chapter_id.in_(affected_ids),
            NarrativeCheckpoint.chapter_snapshot_id.in_(snapshot_ids)
            if snapshot_ids
            else NarrativeCheckpoint.id == "",
        )
    )
    result["removed_rows"]["chapter_checkpoints"] = delete_rows(
        checkpoint_query.all()
    )

    result["removed_rows"]["character_aliases"] = delete_rows(
        db.query(CharacterAlias)
        .filter(CharacterAlias.source_chapter_id.in_(affected_ids))
        .all()
    )
    result["removed_rows"]["character_versions"] = delete_rows(
        db.query(CharacterVersion)
        .filter(CharacterVersion.source_chapter_id.in_(affected_ids))
        .all()
    )
    result["removed_rows"]["worldbuilding_versions"] = delete_rows(
        db.query(WorldbuildingVersion)
        .filter(WorldbuildingVersion.source_chapter_id.in_(affected_ids))
        .all()
    )
    result["removed_rows"]["outline_character_links"] = delete_rows(
        db.query(OutlineNodeCharacter)
        .filter(
            OutlineNodeCharacter.outline_node_id.in_(affected_outline_ids),
            OutlineNodeCharacter.role_in_scene == "建档关联",
        )
        .all()
    )

    catalog_nodes = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == project_id,
            OutlineNode.source_chapter_id.in_(affected_ids),
            OutlineNode.cataloging_status == "cataloged",
        )
        .all()
    )
    for node in sorted(catalog_nodes, key=_catalog_node_depth, reverse=True):
        if node in db.deleted:
            continue
        if node.id in preserved_outline_ids and node.node_type == "chapter":
            reset_chapter_outline_projection(node)
            result["preserved_entities"].append(node.id)
            continue
        if _catalog_node_safe_to_delete(db, node, affected_ids):
            for chapter in ordered_project_chapters(db, project_id):
                if chapter.outline_node_id == node.id:
                    chapter.outline_node_id = None
            db.delete(node)
            result["deleted_outline_nodes"] += 1
        else:
            node.source_chapter_id = None
            node.cataloging_status = None
            result["warnings"].append(
                f"建档大纲节点“{node.title}”已有范围外引用，已保留并解除建档归属"
            )

    facts = (
        db.query(CatalogingFact)
        .filter(
            CatalogingFact.project_id == project_id,
            CatalogingFact.chapter_id.in_(affected_ids),
        )
        .all()
    )
    for fact in facts:
        fact.status = "superseded"
    result["removed_rows"]["cataloging_facts_superseded"] = len(facts)


def _has_governance_lifecycle_outside_suffix(
    db: Session,
    project_id: str,
    item_id: str,
    affected_ids: set[str],
) -> bool:
    events = (
        db.query(NarrativeGovernanceEvent)
        .filter(
            NarrativeGovernanceEvent.project_id == project_id,
            NarrativeGovernanceEvent.item_id == item_id,
        )
        .all()
    )
    return any(
        event.chapter_id is None or event.chapter_id not in affected_ids
        for event in events
    )


def _governance_item_type(row: Any) -> str:
    if isinstance(row, Foreshadowing):
        return "foreshadowings"
    if isinstance(row, CausalEdge):
        return "causal-edges"
    return "narrative-debts"


def _linked_chapter_id(row: Any, affected_ids: set[str]) -> str | None:
    for field in ("source_chapter_id", "target_chapter_id", "resolved_chapter_id"):
        value = getattr(row, field, None)
        if value in affected_ids:
            return str(value)
    return None


def _mark_governance_stale(
    db: Session,
    project_id: str,
    row: Any,
    affected_ids: set[str],
    reason: str,
    result: dict[str, Any],
) -> None:
    previous = str(row.status or "open")
    if previous != "stale":
        row.status = "stale"
        db.add(
            NarrativeGovernanceEvent(
                project_id=project_id,
                item_type=_governance_item_type(row),
                item_id=row.id,
                from_status=previous,
                to_status="stale",
                chapter_id=_linked_chapter_id(row, affected_ids),
                note=reason[:4000],
                actor="chapter_cataloging_rollback",
            )
        )
        result["stale_governance_items"] += 1
    if hasattr(row, "stale_reason"):
        row.stale_reason = reason[:4000]
    if hasattr(row, "last_checked_at"):
        row.last_checked_at = datetime.utcnow()
    for field in ("verified_at", "verification_note", "closed_by"):
        if hasattr(row, field):
            setattr(row, field, None)


def rollback_governance(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    deleted_chapter_ids: set[str],
    deleted_character_ids: set[str],
    reason: str,
    result: dict[str, Any],
) -> None:
    for model in (Foreshadowing, CausalEdge, NarrativeDebt):
        rows = db.query(model).filter(model.project_id == project_id).all()
        for row in rows:
            linked = any(
                getattr(row, field, None) in affected_ids
                for field in (
                    "source_chapter_id",
                    "target_chapter_id",
                    "resolved_chapter_id",
                )
            )
            if not linked:
                if isinstance(row, CausalEdge) and deleted_character_ids:
                    row.character_ids = [
                        item
                        for item in (row.character_ids or [])
                        if str(item) not in deleted_character_ids
                    ]
                continue

            catalog_created = (
                getattr(row, "source", None) == "cataloging"
                and getattr(row, "source_chapter_id", None) in affected_ids
            )
            if catalog_created and not _has_governance_lifecycle_outside_suffix(
                db,
                project_id,
                row.id,
                affected_ids,
            ):
                events = (
                    db.query(NarrativeGovernanceEvent)
                    .filter(
                        NarrativeGovernanceEvent.project_id == project_id,
                        NarrativeGovernanceEvent.item_id == row.id,
                    )
                    .all()
                )
                delete_rows(events)
                db.delete(row)
                result["deleted_governance_items"] += 1
                continue
            if catalog_created:
                result["warnings"].append(
                    f"治理项 {row.id} 在失效章节范围外仍有生命周期记录，已保留并标记待复核"
                )

            _mark_governance_stale(
                db,
                project_id,
                row,
                affected_ids,
                reason,
                result,
            )
            if getattr(row, "source_chapter_id", None) in deleted_chapter_ids:
                row.source_chapter_id = None
            if (
                hasattr(row, "target_chapter_id")
                and row.target_chapter_id in deleted_chapter_ids
            ):
                row.target_chapter_id = None
            if getattr(row, "resolved_chapter_id", None) in deleted_chapter_ids:
                row.resolved_chapter_id = None
                for field in (
                    "resolved_chapter_version",
                    "resolution_note",
                    "resolution_evidence",
                ):
                    if hasattr(row, field):
                        setattr(row, field, None)
            if isinstance(row, CausalEdge) and deleted_character_ids:
                row.character_ids = [
                    item
                    for item in (row.character_ids or [])
                    if str(item) not in deleted_character_ids
                ]


def restore_ledger_projection(
    db: Session,
    project_id: str,
    affected_ids: set[str],
) -> int:
    rows = (
        db.query(CatalogingFact)
        .filter(
            CatalogingFact.project_id == project_id,
            CatalogingFact.fact_type == "narrative_ledger_entry",
            CatalogingFact.chapter_id.notin_(affected_ids),
        )
        .order_by(CatalogingFact.created_at.asc(), CatalogingFact.id.asc())
        .all()
    )
    grouped: dict[tuple[str, str], list[CatalogingFact]] = defaultdict(list)
    for row in rows:
        payload = json_value(row.raw_payload)
        if not isinstance(payload, dict):
            continue
        key = (
            str(payload.get("ledger_type") or "event"),
            str(payload.get("ledger_key") or ""),
        )
        if key[1]:
            grouped[key].append(row)
    restored = 0
    for items in grouped.values():
        for row in items[:-1]:
            row.status = "superseded"
        items[-1].status = "active"
        restored += 1
    return restored


def refresh_character_provenance(
    db: Session,
    project_id: str,
    character_ids: set[str],
) -> None:
    for character_id in character_ids:
        character = db.get(Character, character_id)
        if not character or character in db.deleted:
            continue
        latest_seen = (
            db.query(Chapter)
            .join(ChapterCharacter, ChapterCharacter.chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                ChapterCharacter.character_id == character_id,
            )
            .order_by(
                Chapter.sort_order.desc(),
                Chapter.created_at.desc(),
                Chapter.id.desc(),
            )
            .first()
        )
        latest_updated = (
            db.query(Chapter)
            .join(CharacterVersion, CharacterVersion.source_chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                CharacterVersion.character_id == character_id,
            )
            .order_by(
                Chapter.sort_order.desc(),
                Chapter.created_at.desc(),
                Chapter.id.desc(),
            )
            .first()
        )
        character.last_seen_chapter_id = latest_seen.id if latest_seen else None
        character.last_updated_chapter_id = (
            latest_updated.id if latest_updated else None
        )
        versions = (
            db.query(CharacterVersion.version_number)
            .filter(CharacterVersion.character_id == character_id)
            .all()
        )
        character.current_version = max(
            (int(item[0] or 1) for item in versions),
            default=1,
        )


def refresh_worldbuilding_provenance(
    db: Session,
    project_id: str,
    entry_ids: set[str],
) -> None:
    for entry_id in entry_ids:
        entry = db.get(WorldbuildingEntry, entry_id)
        if not entry or entry in db.deleted:
            continue
        linked = (
            db.query(Chapter)
            .join(ChapterWorldbuilding, ChapterWorldbuilding.chapter_id == Chapter.id)
            .filter(
                Chapter.project_id == project_id,
                ChapterWorldbuilding.worldbuilding_entry_id == entry_id,
            )
            .order_by(
                Chapter.sort_order.asc(),
                Chapter.created_at.asc(),
                Chapter.id.asc(),
            )
            .all()
        )
        version_chapters = (
            db.query(Chapter)
            .join(
                WorldbuildingVersion,
                WorldbuildingVersion.source_chapter_id == Chapter.id,
            )
            .filter(
                Chapter.project_id == project_id,
                WorldbuildingVersion.entry_id == entry_id,
            )
            .order_by(
                Chapter.sort_order.asc(),
                Chapter.created_at.asc(),
                Chapter.id.asc(),
            )
            .all()
        )
        candidates = linked or version_chapters
        entry.first_seen_chapter_id = candidates[0].id if candidates else None
        entry.last_updated_chapter_id = (
            version_chapters[-1].id if version_chapters else None
        )


def invalidate_cataloging_audit(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    reason: str,
    result: dict[str, Any],
) -> None:
    candidates = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.project_id == project_id,
            CatalogingCandidate.chapter_id.in_(affected_ids),
        )
        .all()
    )
    for candidate in candidates:
        candidate.status = "superseded"
        candidate.error = reason[:2000]
        candidate.updated_at = datetime.utcnow()
    result["invalidated_candidates"] = len(candidates)

    runs = (
        db.query(CatalogingChapterRun)
        .filter(
            CatalogingChapterRun.project_id == project_id,
            CatalogingChapterRun.chapter_id.in_(affected_ids),
        )
        .all()
    )
    job_ids: set[str] = set()
    for run in runs:
        run.status = "skipped_by_user"
        run.error = reason[:2000]
        run.review_warning = "章节顺序或语义已变化，旧建档投影已回退"
        run.completed_at = datetime.utcnow()
        job_ids.add(run.job_id)
    result["invalidated_runs"] = len(runs)

    jobs = (
        db.query(CatalogingJob).filter(CatalogingJob.id.in_(job_ids)).all()
        if job_ids
        else []
    )
    for job in jobs:
        job.status = "cancelled"
        job.context_integrity = "stale"
        job.error = reason[:2000]
        job.current_chapter_id = None
        job.blocked_chapter_id = None
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
    result["cancelled_jobs"] = len(jobs)
    result["cancelled_job_ids"] = sorted(job_ids)


def _delete_fts_rows(db: Session, chunk_ids: set[str]) -> None:
    if not chunk_ids or not db.bind or db.bind.dialect.name != "sqlite":
        return
    exists = db.execute(
        text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'rag_chunks_fts'"
        )
    ).first()
    if not exists:
        return
    db.execute(
        text("DELETE FROM rag_chunks_fts WHERE chunk_id = :chunk_id"),
        [{"chunk_id": chunk_id} for chunk_id in sorted(chunk_ids)],
    )


def clear_rag_projection(
    db: Session,
    project_id: str,
    chapter_ids: set[str],
    character_ids: set[str],
    world_ids: set[str],
    outline_ids: set[str],
) -> int:
    source_pairs = {
        *(("chapter", item) for item in chapter_ids),
        *(("chapter_summary", item) for item in chapter_ids),
        *(("character", item) for item in character_ids),
        *(("character_timeline", item) for item in character_ids),
        *(("worldbuilding", item) for item in world_ids),
        *(("outline", item) for item in outline_ids),
    }
    documents = (
        db.query(RagDocument)
        .filter(RagDocument.project_id == project_id)
        .all()
    )
    doomed = [
        row
        for row in documents
        if (row.source_type, row.source_id) in source_pairs
    ]
    document_ids = {row.id for row in doomed}
    chunks = (
        db.query(RagChunk).filter(RagChunk.document_id.in_(document_ids)).all()
        if document_ids
        else []
    )
    _delete_fts_rows(db, {row.id for row in chunks})
    delete_rows(chunks)
    delete_rows(doomed)
    return len(doomed)


__all__ = [
    "cleanup_chapter_owned_rows",
    "clear_rag_projection",
    "invalidate_cataloging_audit",
    "refresh_character_provenance",
    "refresh_worldbuilding_provenance",
    "restore_ledger_projection",
    "rollback_governance",
]
