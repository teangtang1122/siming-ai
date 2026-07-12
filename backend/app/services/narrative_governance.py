"""Structured narrative governance for long-form projects."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database.models import (
    CausalEdge,
    Chapter,
    ChapterSnapshot,
    ChapterQualityMetric,
    CharacterNarrativeState,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
)
from .chapter_service import diff_snapshots, restore_chapter_from_snapshot


OPEN_STATUSES = {"open", "deferred", "pending_review"}
FINAL_STATUSES = {"fulfilled", "resolved", "abandoned", "invalidated"}
IMPORTANCE_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _clean(value: Any, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def dedupe_key(*values: Any) -> str:
    canonical = "|".join(re.sub(r"[\W_]+", "", _clean(value).lower()) for value in values)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:32]


def _serialize(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _chapter_number_map(db: Session, project_id: str) -> dict[str, int]:
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.created_at, Chapter.id).all()
    return {chapter.id: index for index, chapter in enumerate(chapters, start=1)}


def upsert_foreshadowing(db: Session, project_id: str, data: dict[str, Any]) -> Foreshadowing:
    title = _clean(data.get("title") or data.get("description"), 500)
    if not title:
        raise ValueError("伏笔标题不能为空")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(title, data.get("storyline"))
    row = db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id, Foreshadowing.dedupe_key == key).first()
    if not row:
        row = Foreshadowing(project_id=project_id, title=title, dedupe_key=key)
        db.add(row)
    for field in ("description", "status", "importance", "source_chapter_id", "target_chapter_id", "target_chapter_number", "resolved_chapter_id", "evidence", "storyline", "source"):
        if field in data:
            setattr(row, field, data[field])
    row.title = title
    if row.status in {"fulfilled", "abandoned"} and data.get("chapter_id") and not row.resolved_chapter_id:
        row.resolved_chapter_id = data["chapter_id"]
    db.flush()
    return row


def upsert_causal_edge(db: Session, project_id: str, data: dict[str, Any]) -> CausalEdge:
    cause = _clean(data.get("cause"), 2000)
    effect = _clean(data.get("effect"), 2000)
    if not cause or not effect:
        raise ValueError("因果边必须包含原因和结果")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(cause, effect, data.get("causal_type"))
    row = db.query(CausalEdge).filter(CausalEdge.project_id == project_id, CausalEdge.dedupe_key == key).first()
    if not row:
        row = CausalEdge(project_id=project_id, cause=cause, effect=effect, dedupe_key=key)
        db.add(row)
    row.cause = cause
    row.effect = effect
    for field in ("causal_type", "strength", "status", "character_ids", "source_chapter_id", "resolved_chapter_id", "evidence", "source"):
        if field in data:
            setattr(row, field, data[field])
    row.strength = max(0.0, min(1.0, float(row.strength or 0.5)))
    db.flush()
    return row


def upsert_narrative_debt(db: Session, project_id: str, data: dict[str, Any]) -> NarrativeDebt:
    title = _clean(data.get("title") or data.get("description"), 500)
    if not title:
        raise ValueError("叙事债务标题不能为空")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(data.get("debt_type"), title)
    row = db.query(NarrativeDebt).filter(NarrativeDebt.project_id == project_id, NarrativeDebt.dedupe_key == key).first()
    if not row:
        row = NarrativeDebt(project_id=project_id, title=title, dedupe_key=key)
        db.add(row)
    row.title = title
    for field in ("debt_type", "description", "status", "priority", "source_chapter_id", "target_chapter_id", "target_chapter_number", "resolved_chapter_id", "linked_foreshadowing_id", "linked_causal_edge_id", "evidence", "source"):
        if field in data:
            setattr(row, field, data[field])
    db.flush()
    return row


def record_character_state(db: Session, project_id: str, data: dict[str, Any]) -> CharacterNarrativeState:
    if not data.get("character_id"):
        raise ValueError("character_id is required")
    row = CharacterNarrativeState(project_id=project_id, **{
        key: data.get(key) for key in (
            "character_id", "chapter_id", "current_goal", "public_stance", "hidden_intent",
            "emotional_residue", "relationship_tension", "behavior_boundaries", "evidence", "source",
        ) if data.get(key) is not None
    })
    db.add(row)
    db.flush()
    return row


def record_quality_metric(db: Session, project_id: str, data: dict[str, Any]) -> ChapterQualityMetric:
    if not data.get("chapter_id"):
        raise ValueError("chapter_id is required")
    score_fields = ("plot_tension", "emotional_tension", "pacing_density", "character_consistency", "viewpoint_consistency", "world_consistency", "target_tension")
    values = {key: max(0.0, min(100.0, float(data[key]))) for key in score_fields if data.get(key) is not None}
    warnings = list(data.get("warnings") or [])
    assessed = [values[key] for key in score_fields[:-1] if key in values]
    passed = data.get("passed")
    if passed is None and assessed:
        passed = min(assessed) >= 60
    row = ChapterQualityMetric(
        project_id=project_id,
        chapter_id=data["chapter_id"],
        strict_mode=bool(data.get("strict_mode", False)),
        passed=passed,
        warnings=warnings,
        evidence=_clean(data.get("evidence"), 4000) or None,
        source=_clean(data.get("source"), 50) or "manual",
        **values,
    )
    db.add(row)
    db.flush()
    return row


def apply_governance_candidates(db: Session, project_id: str, candidates: list[dict[str, Any]], *, chapter_id: str | None = None) -> list[dict[str, Any]]:
    results = []
    for candidate in candidates:
        item = dict(candidate)
        item.setdefault("source_chapter_id", chapter_id)
        item.setdefault("chapter_id", chapter_id)
        item.setdefault("source", "candidate")
        kind = str(item.get("type") or item.get("item_type") or "").lower()
        if kind in {"foreshadowing", "foreshadow", "narrative_promise"}:
            row = upsert_foreshadowing(db, project_id, item)
        elif kind in {"causal_edge", "causal"}:
            row = upsert_causal_edge(db, project_id, item)
        elif kind in {"narrative_debt", "debt"}:
            row = upsert_narrative_debt(db, project_id, item)
        elif kind in {"character_state", "character_narrative_state", "character_mask", "emotion_ledger"}:
            row = record_character_state(db, project_id, item)
        elif kind in {"chapter_quality", "quality_metric", "tension_dimensions"}:
            row = record_quality_metric(db, project_id, item)
        else:
            continue
        results.append({"type": kind, "item": _serialize(row)})
    return results


def governance_dashboard(db: Session, project_id: str, *, chapter_id: str = "", view: str = "all") -> dict[str, Any]:
    numbers = _chapter_number_map(db, project_id)
    current_number = numbers.get(chapter_id) if chapter_id else None
    foreshadows = db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id).all()
    causal_edges = db.query(CausalEdge).filter(CausalEdge.project_id == project_id).all()
    debts = db.query(NarrativeDebt).filter(NarrativeDebt.project_id == project_id).all()

    def relevant(item: Any) -> bool:
        if view == "chapter" and chapter_id:
            return chapter_id in {getattr(item, "source_chapter_id", None), getattr(item, "target_chapter_id", None), getattr(item, "resolved_chapter_id", None)}
        if view == "due":
            target = getattr(item, "target_chapter_number", None)
            return item.status in OPEN_STATUSES and target is not None and (current_number is None or target <= current_number + 3)
        if view == "risk":
            level = getattr(item, "importance", None) or getattr(item, "priority", None)
            return item.status in OPEN_STATUSES and (level in {"critical", "high"} or getattr(item, "strength", 0) >= 0.75)
        return True

    foreshadow_items = [_serialize(row) for row in foreshadows if relevant(row)]
    causal_items = [_serialize(row) for row in causal_edges if relevant(row)]
    debt_items = [_serialize(row) for row in debts if relevant(row)]
    states = db.query(CharacterNarrativeState).filter(CharacterNarrativeState.project_id == project_id).order_by(CharacterNarrativeState.created_at.desc()).limit(100).all()
    latest_states: dict[str, dict[str, Any]] = {}
    for row in states:
        latest_states.setdefault(row.character_id, _serialize(row))
    metrics = db.query(ChapterQualityMetric).filter(ChapterQualityMetric.project_id == project_id).order_by(ChapterQualityMetric.created_at.desc()).limit(200).all()
    checkpoints = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.project_id == project_id).order_by(NarrativeCheckpoint.sequence.desc()).limit(30).all()
    return {
        "foreshadowings": foreshadow_items,
        "causal_edges": causal_items,
        "narrative_debts": debt_items,
        "character_states": list(latest_states.values()),
        "quality_metrics": [_serialize(row) for row in metrics],
        "checkpoints": [{key: value for key, value in _serialize(row).items() if key != "state_json"} for row in checkpoints],
        "counts": {
            "open_foreshadowings": sum(row.status in OPEN_STATUSES for row in foreshadows),
            "open_causal_edges": sum(row.status == "open" for row in causal_edges),
            "open_debts": sum(row.status in OPEN_STATUSES for row in debts),
            "high_risk": sum(relevant(row) for row in [*foreshadows, *causal_edges, *debts]) if view == "risk" else None,
        },
    }


def governance_context(db: Session, project_id: str, *, chapter_id: str | None = None, limit: int = 12) -> str:
    dashboard = governance_dashboard(db, project_id, chapter_id=chapter_id or "", view="all")
    items: list[tuple[int, str]] = []
    for row in dashboard["narrative_debts"]:
        if row["status"] in OPEN_STATUSES:
            weight = IMPORTANCE_WEIGHT.get(row.get("priority"), 2)
            items.append((weight + 4, f"[叙事债务/{row['priority']}] {row['title']}"))
    for row in dashboard["foreshadowings"]:
        if row["status"] in OPEN_STATUSES:
            weight = IMPORTANCE_WEIGHT.get(row.get("importance"), 2)
            due = f"，目标第{row['target_chapter_number']}章" if row.get("target_chapter_number") else ""
            items.append((weight + 3, f"[伏笔/{row['importance']}] {row['title']}{due}"))
    for row in dashboard["causal_edges"]:
        if row["status"] == "open":
            items.append((int(float(row.get("strength") or 0) * 5) + 2, f"[未闭环因果] {row['cause']} -> {row['effect']}"))
    for row in dashboard["character_states"]:
        details = "；".join(filter(None, [row.get("current_goal"), row.get("emotional_residue"), row.get("behavior_boundaries")]))
        if details:
            items.append((4, f"[角色动态/{row['character_id']}] {details}"))
    items.sort(key=lambda item: item[0], reverse=True)
    return "叙事治理锁：\n" + "\n".join(text for _, text in items[:limit]) if items else ""


def _snapshot_state(db: Session, project_id: str) -> dict[str, Any]:
    return {
        "foreshadowings": [_serialize(row) for row in db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id).all()],
        "causal_edges": [_serialize(row) for row in db.query(CausalEdge).filter(CausalEdge.project_id == project_id).all()],
        "narrative_debts": [_serialize(row) for row in db.query(NarrativeDebt).filter(NarrativeDebt.project_id == project_id).all()],
        "character_states": [_serialize(row) for row in db.query(CharacterNarrativeState).filter(CharacterNarrativeState.project_id == project_id).all()],
        "quality_metrics": [_serialize(row) for row in db.query(ChapterQualityMetric).filter(ChapterQualityMetric.project_id == project_id).all()],
    }


def create_narrative_checkpoint(db: Session, project_id: str, *, chapter: Chapter | None = None, label: str = "", trigger_type: str = "post_write") -> NarrativeCheckpoint:
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
    sequence = (db.query(func.max(NarrativeCheckpoint.sequence)).filter(NarrativeCheckpoint.project_id == project_id).scalar() or 0) + 1
    checkpoint = NarrativeCheckpoint(
        project_id=project_id,
        chapter_id=chapter.id if chapter else None,
        chapter_snapshot_id=snapshot_id,
        sequence=sequence,
        label=_clean(label, 300) or (f"{chapter.title} 写后状态" if chapter else f"叙事检查点 {sequence}"),
        trigger_type=trigger_type,
        state_json=_snapshot_state(db, project_id),
    )
    db.add(checkpoint)
    db.flush()
    return checkpoint


def restore_narrative_checkpoint(db: Session, project_id: str, checkpoint_id: str) -> NarrativeCheckpoint:
    checkpoint = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.id == checkpoint_id, NarrativeCheckpoint.project_id == project_id).first()
    if not checkpoint:
        raise ValueError("叙事检查点不存在")
    if checkpoint.chapter_id and checkpoint.chapter_snapshot_id:
        chapter = db.query(Chapter).filter(Chapter.id == checkpoint.chapter_id, Chapter.project_id == project_id).first()
        snapshot = db.query(ChapterSnapshot).filter(ChapterSnapshot.id == checkpoint.chapter_snapshot_id, ChapterSnapshot.chapter_id == checkpoint.chapter_id).first()
        if not chapter or not snapshot:
            raise ValueError("检查点关联的章节版本不存在")
        restore_chapter_from_snapshot(db, chapter, snapshot)
    state = checkpoint.state_json or {}
    for model in (NarrativeDebt, CharacterNarrativeState, ChapterQualityMetric, CausalEdge, Foreshadowing):
        db.query(model).filter(model.project_id == project_id).delete(synchronize_session="fetch")
    db.flush()
    db.expunge_all()
    mapping = {
        "foreshadowings": Foreshadowing,
        "causal_edges": CausalEdge,
        "narrative_debts": NarrativeDebt,
        "character_states": CharacterNarrativeState,
        "quality_metrics": ChapterQualityMetric,
    }
    for key, model in mapping.items():
        valid = {column.name for column in model.__table__.columns}
        for raw in state.get(key) or []:
            values = {name: value for name, value in raw.items() if name in valid and name not in {"created_at", "updated_at"}}
            db.add(model(**values))
    db.flush()
    return checkpoint


def checkpoint_diff(db: Session, project_id: str, checkpoint_id: str) -> dict[str, Any]:
    checkpoint = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.id == checkpoint_id, NarrativeCheckpoint.project_id == project_id).first()
    if not checkpoint:
        raise ValueError("叙事检查点不存在")
    current = _snapshot_state(db, project_id)
    saved = checkpoint.state_json or {}
    changes = {}
    for key in current:
        saved_by_id = {item["id"]: item for item in saved.get(key) or []}
        current_by_id = {item["id"]: item for item in current.get(key) or []}
        changes[key] = {
            "added": [item for item_id, item in current_by_id.items() if item_id not in saved_by_id],
            "removed": [item for item_id, item in saved_by_id.items() if item_id not in current_by_id],
            "changed": [{"before": saved_by_id[item_id], "after": current_by_id[item_id]} for item_id in saved_by_id.keys() & current_by_id.keys() if saved_by_id[item_id] != current_by_id[item_id]],
        }
    chapter_changes = None
    if checkpoint.chapter_id and checkpoint.chapter_snapshot_id:
        saved_snapshot = db.query(ChapterSnapshot).filter(ChapterSnapshot.id == checkpoint.chapter_snapshot_id).first()
        current_snapshot = (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == checkpoint.chapter_id)
            .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
            .first()
        )
        if saved_snapshot and current_snapshot:
            chapter_changes = diff_snapshots(saved_snapshot, current_snapshot)
    return {"checkpoint": _serialize(checkpoint), "chapter_changes": chapter_changes, "changes": changes}
