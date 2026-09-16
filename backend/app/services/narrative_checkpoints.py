"""Canonical narrative checkpoint capture, restoration and comparison."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database.models import (
    CausalEdge,
    Chapter,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSnapshot,
    CharacterNarrativeState,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
)
from .chapter_service import diff_snapshots, restore_chapter_from_snapshot
from .narrative_governance_records import _chapter, _clean, _serialize


def _snapshot_state(db: Session, project_id: str) -> dict[str, Any]:
    return {
        "foreshadowings": [
            _serialize(row)
            for row in db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id).all()
        ],
        "causal_edges": [
            _serialize(row)
            for row in db.query(CausalEdge).filter(CausalEdge.project_id == project_id).all()
        ],
        "narrative_debts": [
            _serialize(row)
            for row in db.query(NarrativeDebt).filter(NarrativeDebt.project_id == project_id).all()
        ],
        "character_states": [
            _serialize(row)
            for row in db.query(CharacterNarrativeState)
            .filter(CharacterNarrativeState.project_id == project_id)
            .all()
        ],
        "quality_metrics": [
            _serialize(row)
            for row in db.query(ChapterQualityMetric)
            .filter(ChapterQualityMetric.project_id == project_id)
            .all()
        ],
        "chapter_reviews": [
            _serialize(row)
            for row in db.query(ChapterGovernanceReview)
            .filter(ChapterGovernanceReview.project_id == project_id)
            .all()
        ],
    }


def create_narrative_checkpoint(
    db: Session,
    project_id: str,
    *,
    chapter: Chapter | None = None,
    label: str = "",
    trigger_type: str = "post_write",
    review_summary: dict[str, Any] | None = None,
) -> NarrativeCheckpoint:
    snapshot_id = None
    if chapter:
        db.flush()
        snapshot = (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == chapter.id)
            .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
            .first()
        )
        snapshot_id = snapshot.id if snapshot else None
    sequence = (
        db.query(func.max(NarrativeCheckpoint.sequence))
        .filter(NarrativeCheckpoint.project_id == project_id)
        .scalar()
        or 0
    ) + 1
    state = _snapshot_state(db, project_id)
    if review_summary:
        state["_review"] = review_summary
    checkpoint = NarrativeCheckpoint(
        project_id=project_id,
        chapter_id=chapter.id if chapter else None,
        chapter_snapshot_id=snapshot_id,
        sequence=sequence,
        label=_clean(label, 300)
        or (f"{chapter.title} 写后状态" if chapter else f"叙事检查点 {sequence}"),
        trigger_type=trigger_type,
        state_json=state,
    )
    db.add(checkpoint)
    db.flush()
    return checkpoint


def restore_narrative_checkpoint(
    db: Session, project_id: str, checkpoint_id: str
) -> NarrativeCheckpoint:
    checkpoint = (
        db.query(NarrativeCheckpoint)
        .filter(
            NarrativeCheckpoint.id == checkpoint_id, NarrativeCheckpoint.project_id == project_id
        )
        .first()
    )
    if not checkpoint:
        raise ValueError("叙事检查点不存在")
    safety_chapter = _chapter(db, project_id, checkpoint.chapter_id)
    create_narrative_checkpoint(
        db,
        project_id,
        chapter=safety_chapter,
        label=f"回滚至 #{checkpoint.sequence} 前的安全点",
        trigger_type="pre_restore_safety",
        review_summary={"restoring_checkpoint_id": checkpoint.id},
    )
    if checkpoint.chapter_id and checkpoint.chapter_snapshot_id:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.id == checkpoint.chapter_id, Chapter.project_id == project_id)
            .first()
        )
        snapshot = (
            db.query(ChapterSnapshot)
            .filter(
                ChapterSnapshot.id == checkpoint.chapter_snapshot_id,
                ChapterSnapshot.chapter_id == checkpoint.chapter_id,
            )
            .first()
        )
        if not chapter or not snapshot:
            raise ValueError("检查点关联的章节版本不存在")
        restore_chapter_from_snapshot(db, chapter, snapshot)
    state = checkpoint.state_json or {}
    for model in (
        ChapterGovernanceReview,
        NarrativeDebt,
        CharacterNarrativeState,
        ChapterQualityMetric,
        CausalEdge,
        Foreshadowing,
    ):
        db.query(model).filter(model.project_id == project_id).delete(synchronize_session="fetch")
    db.flush()
    db.expunge_all()
    mapping = {
        "foreshadowings": Foreshadowing,
        "causal_edges": CausalEdge,
        "narrative_debts": NarrativeDebt,
        "character_states": CharacterNarrativeState,
        "quality_metrics": ChapterQualityMetric,
        "chapter_reviews": ChapterGovernanceReview,
    }
    for key, model in mapping.items():
        valid = {column.name for column in model.__table__.columns}
        for raw in state.get(key) or []:
            values = {
                name: value
                for name, value in raw.items()
                if name in valid and name not in {"created_at", "updated_at"}
            }
            db.add(model(**values))
    db.flush()
    return checkpoint


def checkpoint_diff(db: Session, project_id: str, checkpoint_id: str) -> dict[str, Any]:
    checkpoint = (
        db.query(NarrativeCheckpoint)
        .filter(
            NarrativeCheckpoint.id == checkpoint_id, NarrativeCheckpoint.project_id == project_id
        )
        .first()
    )
    if not checkpoint:
        raise ValueError("叙事检查点不存在")
    current = _snapshot_state(db, project_id)
    saved = checkpoint.state_json or {}
    changes = {}
    for key in current:
        saved_by_id = {item["id"]: item for item in saved.get(key) or []}
        current_by_id = {item["id"]: item for item in current.get(key) or []}
        changes[key] = {
            "added": [
                item for item_id, item in current_by_id.items() if item_id not in saved_by_id
            ],
            "removed": [
                item for item_id, item in saved_by_id.items() if item_id not in current_by_id
            ],
            "changed": [
                {"before": saved_by_id[item_id], "after": current_by_id[item_id]}
                for item_id in saved_by_id.keys() & current_by_id.keys()
                if saved_by_id[item_id] != current_by_id[item_id]
            ],
        }
    chapter_changes = None
    if checkpoint.chapter_id and checkpoint.chapter_snapshot_id:
        saved_snapshot = (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.id == checkpoint.chapter_snapshot_id)
            .first()
        )
        current_snapshot = (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == checkpoint.chapter_id)
            .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
            .first()
        )
        if saved_snapshot and current_snapshot:
            chapter_changes = diff_snapshots(saved_snapshot, current_snapshot)
    return {
        "checkpoint": _serialize(checkpoint),
        "chapter_changes": chapter_changes,
        "changes": changes,
    }
