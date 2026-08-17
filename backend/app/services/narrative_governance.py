"""Structured narrative governance for long-form projects."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database.models import (
    CausalEdge,
    Chapter,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSnapshot,
    Character,
    CharacterNarrativeState,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
    NarrativeGovernanceEvent,
)
from ..modules.continuity.domain.governance_lifecycle import ALLOWED_STATUSES, normalize_item_type
from .chapter_service import diff_snapshots, restore_chapter_from_snapshot
from .narrative_source_locator import resolve_narrative_source_range
from .story_granularity import normalize_chapter_narrative_state

OPEN_STATUSES = {"open", "deferred", "pending_review", "stale"}
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
    chapters = db.query(Chapter).filter(Chapter.project_id == project_id).order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc()).all()
    return {chapter.id: index for index, chapter in enumerate(chapters, start=1)}


def _chapter(db: Session, project_id: str, chapter_id: str | None) -> Chapter | None:
    if not chapter_id:
        return None
    return (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.id == chapter_id)
        .first()
    )


def _record_event(
    db: Session,
    project_id: str,
    item_type: str,
    item_id: str,
    *,
    from_status: str | None,
    to_status: str,
    chapter: Chapter | None = None,
    note: str = "",
    actor: str = "system",
) -> None:
    db.add(
        NarrativeGovernanceEvent(
            project_id=project_id,
            item_type=normalize_item_type(item_type),
            item_id=item_id,
            from_status=from_status,
            to_status=to_status,
            chapter_id=chapter.id if chapter else None,
            chapter_version=(chapter.current_version or 1) if chapter else None,
            note=_clean(note, 4000) or None,
            actor=_clean(actor, 50) or "system",
        )
    )


def _find_governance_row(
    db: Session,
    model: type,
    project_id: str,
    data: dict[str, Any],
    fallback_key: str,
) -> tuple[Any | None, bool]:
    """Resolve stable identity before falling back to a generated content hash."""

    explicit_id = _clean(
        data.get("resolves_item_id")
        or data.get("governance_item_id")
        or data.get("item_id"),
        36,
    )
    if explicit_id:
        row = db.query(model).filter(model.project_id == project_id, model.id == explicit_id).first()
        if not row:
            raise ValueError("治理候选引用的原记录不存在或不属于当前作品")
        return row, True
    resolution_key = _clean(data.get("resolves_dedupe_key"), 200)
    if resolution_key:
        row = (
            db.query(model)
            .filter(model.project_id == project_id, model.dedupe_key == resolution_key)
            .first()
        )
        if not row:
            raise ValueError("治理候选引用的原去重键不存在")
        return row, True
    explicit_key = _clean(data.get("dedupe_key"), 200)
    key = explicit_key or fallback_key
    row = db.query(model).filter(model.project_id == project_id, model.dedupe_key == key).first()
    return row, bool(row and explicit_key)


def _prepare_candidate_status(
    row: Any,
    data: dict[str, Any],
    *,
    item_type: str,
    chapter_id: str | None,
    matched_by_reference: bool,
) -> tuple[str, str | None]:
    requested = str(data.get("status") or row.status or "open").strip().lower()
    _validate_candidate_status(item_type, requested)
    explicitly_submitted = "status" in data and requested in {"pending_review", *FINAL_STATUSES}
    if row.status in FINAL_STATUSES and requested != row.status:
        raise ValueError("已关闭治理项必须先由用户重新打开，候选任务不能覆盖终态")
    if explicitly_submitted and row.status in FINAL_STATUSES:
        raise ValueError("已关闭治理项必须先由用户重新打开，候选任务不能覆盖终态")
    if explicitly_submitted and requested in {"abandoned", "invalidated"}:
        raise ValueError("放弃或作废治理项必须由用户显式确认")
    if explicitly_submitted and not matched_by_reference:
        raise ValueError("提交解决候选必须携带原治理项 resolves_item_id 或 resolves_dedupe_key")
    if requested not in FINAL_STATUSES:
        if requested == "pending_review" and explicitly_submitted:
            row.resolved_chapter_id = (
                data.get("resolved_chapter_id") or chapter_id or row.resolved_chapter_id
            )
            row.resolution_note = _clean(
                data.get("resolution_note")
                or data.get("resolution_evidence")
                or data.get("evidence"),
                4000,
            )
            if not row.resolved_chapter_id or len(row.resolution_note or "") < 4:
                raise ValueError("提交复检必须绑定解决章节并填写解决说明")
        return requested, None
    row.resolved_chapter_id = (
        data.get("resolved_chapter_id") or chapter_id or row.resolved_chapter_id
    )
    row.resolution_note = _clean(
        data.get("resolution_note")
        or data.get("resolution_evidence")
        or data.get("evidence")
        or "模型检测到可能已解决，等待人工复检",
        4000,
    )
    # Model/candidate writes may submit a repair for review, but only a user or
    # an explicit verification action may close the lifecycle.
    return "pending_review", requested


def _is_resolution_submission(data: dict[str, Any]) -> bool:
    return "status" in data and str(data.get("status") or "").strip().lower() in {
        "pending_review",
        *FINAL_STATUSES,
    }


def _has_stable_governance_reference(data: dict[str, Any]) -> bool:
    return bool(
        data.get("resolves_item_id")
        or data.get("governance_item_id")
        or data.get("resolves_dedupe_key")
    )


def _apply_governance_fields(
    row: Any,
    data: dict[str, Any],
    fields: tuple[str, ...],
    *,
    matched_by_reference: bool,
) -> None:
    """Keep discovery evidence/source immutable when a candidate submits a repair."""

    resolution_submission = matched_by_reference and _is_resolution_submission(data)
    if resolution_submission and data.get("evidence") and not data.get("resolution_evidence"):
        row.resolution_evidence = data["evidence"]
    for field in fields:
        if resolution_submission and field in {"source_chapter_id", "evidence"}:
            continue
        if field in data:
            setattr(row, field, data[field])


def _validated_chapters(
    db: Session,
    project_id: str,
    row: Any,
) -> tuple[Chapter | None, Chapter | None]:
    """Validate every chapter foreign key before persisting a governance item."""

    source_chapter = _chapter(db, project_id, getattr(row, "source_chapter_id", None))
    if getattr(row, "source_chapter_id", None) and not source_chapter:
        raise ValueError("来源章节不存在或不属于当前作品")
    target_chapter_id = getattr(row, "target_chapter_id", None)
    if target_chapter_id and not _chapter(db, project_id, target_chapter_id):
        raise ValueError("计划处理章节不存在或不属于当前作品")
    resolved_chapter = _chapter(db, project_id, getattr(row, "resolved_chapter_id", None))
    if getattr(row, "resolved_chapter_id", None) and not resolved_chapter:
        raise ValueError("解决章节不存在或不属于当前作品")
    return source_chapter, resolved_chapter


def _source_locator_for_row(row: Any, chapter: Chapter | None) -> dict[str, Any] | None:
    if not chapter:
        return None
    hints = [
        getattr(row, field, None)
        for field in ("title", "description", "cause", "effect", "resolution_note")
    ]
    return resolve_narrative_source_range(
        chapter.content or "",
        evidence=str(getattr(row, "evidence", "") or ""),
        hints=hints,
    )


def _ensure_source_evidence(row: Any, chapter: Chapter | None) -> None:
    location = _source_locator_for_row(row, chapter)
    if location and not _clean(getattr(row, "evidence", None), 4000):
        row.evidence = location["source_excerpt"]


def _validate_candidate_status(item_type: str, status: str) -> str:
    normalized = normalize_item_type(item_type)
    if status not in ALLOWED_STATUSES[normalized]:
        raise ValueError("治理候选包含不适用于该对象类型的状态")
    return status


def upsert_foreshadowing(db: Session, project_id: str, data: dict[str, Any]) -> Foreshadowing:
    title = _clean(data.get("title") or data.get("description"), 500)
    if not title:
        raise ValueError("伏笔标题不能为空")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(title, data.get("storyline"))
    row, matched_by_reference = _find_governance_row(
        db, Foreshadowing, project_id, data, key
    )
    created = row is None
    if not row:
        row = Foreshadowing(project_id=project_id, title=title, dedupe_key=key)
    previous_status = None if created else row.status
    requested_status, _ = _prepare_candidate_status(
        row,
        data,
        item_type="foreshadowings",
        chapter_id=data.get("chapter_id") or data.get("source_chapter_id"),
        matched_by_reference=matched_by_reference,
    )
    _apply_governance_fields(
        row,
        data,
        (
            "description",
            "importance",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "evidence",
            "storyline",
            "source",
            "resolution_note",
            "resolution_evidence",
        ),
        matched_by_reference=matched_by_reference,
    )
    if created or not matched_by_reference:
        row.title = title
    row.status = requested_status
    source_chapter, resolved_chapter = _validated_chapters(db, project_id, row)
    _ensure_source_evidence(row, source_chapter)
    if source_chapter and row.source_chapter_version is None:
        row.source_chapter_version = source_chapter.current_version or 1
    if resolved_chapter:
        row.resolved_chapter_version = resolved_chapter.current_version or 1
    if created:
        db.add(row)
    db.flush()
    if created or previous_status != row.status:
        _record_event(
            db,
            project_id,
            "foreshadowings",
            row.id,
            from_status=previous_status,
            to_status=row.status,
            chapter=resolved_chapter or source_chapter,
            note=row.resolution_note or row.evidence or "治理项已登记",
            actor=_clean(data.get("source"), 50) or "candidate",
        )
    return row


def upsert_causal_edge(db: Session, project_id: str, data: dict[str, Any]) -> CausalEdge:
    cause = _clean(data.get("cause"), 2000)
    effect = _clean(data.get("effect"), 2000)
    if not cause or not effect:
        raise ValueError("因果边必须包含原因和结果")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(cause, effect, data.get("causal_type"))
    row, matched_by_reference = _find_governance_row(db, CausalEdge, project_id, data, key)
    created = row is None
    if not row:
        row = CausalEdge(project_id=project_id, cause=cause, effect=effect, dedupe_key=key)
    previous_status = None if created else row.status
    requested_status, _ = _prepare_candidate_status(
        row,
        data,
        item_type="causal-edges",
        chapter_id=data.get("chapter_id") or data.get("source_chapter_id"),
        matched_by_reference=matched_by_reference,
    )
    if created or not matched_by_reference:
        row.cause = cause
        row.effect = effect
    _apply_governance_fields(
        row,
        data,
        (
            "causal_type",
            "strength",
            "character_ids",
            "source_chapter_id",
            "resolved_chapter_id",
            "evidence",
            "source",
            "resolution_note",
            "resolution_evidence",
        ),
        matched_by_reference=matched_by_reference,
    )
    row.status = requested_status
    row.strength = max(0.0, min(1.0, float(row.strength or 0.5)))
    source_chapter, resolved_chapter = _validated_chapters(db, project_id, row)
    _ensure_source_evidence(row, source_chapter)
    if source_chapter and row.source_chapter_version is None:
        row.source_chapter_version = source_chapter.current_version or 1
    if resolved_chapter:
        row.resolved_chapter_version = resolved_chapter.current_version or 1
    if created:
        db.add(row)
    db.flush()
    if created or previous_status != row.status:
        _record_event(
            db,
            project_id,
            "causal-edges",
            row.id,
            from_status=previous_status,
            to_status=row.status,
            chapter=resolved_chapter or source_chapter,
            note=row.resolution_note or row.evidence or "因果项已登记",
            actor=_clean(data.get("source"), 50) or "candidate",
        )
    return row


def upsert_narrative_debt(db: Session, project_id: str, data: dict[str, Any]) -> NarrativeDebt:
    title = _clean(data.get("title") or data.get("description"), 500)
    if not title:
        raise ValueError("叙事债务标题不能为空")
    key = _clean(data.get("dedupe_key"), 200) or dedupe_key(data.get("debt_type"), title)
    row, matched_by_reference = _find_governance_row(db, NarrativeDebt, project_id, data, key)
    created = row is None
    if not row:
        row = NarrativeDebt(project_id=project_id, title=title, dedupe_key=key)
    previous_status = None if created else row.status
    requested_status, _ = _prepare_candidate_status(
        row,
        data,
        item_type="narrative-debts",
        chapter_id=data.get("chapter_id") or data.get("source_chapter_id"),
        matched_by_reference=matched_by_reference,
    )
    if created or not matched_by_reference:
        row.title = title
    _apply_governance_fields(
        row,
        data,
        (
            "debt_type",
            "description",
            "priority",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "linked_foreshadowing_id",
            "linked_causal_edge_id",
            "evidence",
            "source",
            "resolution_note",
            "resolution_evidence",
        ),
        matched_by_reference=matched_by_reference,
    )
    if row.linked_foreshadowing_id and not db.query(Foreshadowing).filter(
        Foreshadowing.id == row.linked_foreshadowing_id,
        Foreshadowing.project_id == project_id,
    ).first():
        raise ValueError("关联伏笔不存在或不属于当前作品")
    if row.linked_causal_edge_id and not db.query(CausalEdge).filter(
        CausalEdge.id == row.linked_causal_edge_id,
        CausalEdge.project_id == project_id,
    ).first():
        raise ValueError("关联因果项不存在或不属于当前作品")
    row.status = requested_status
    source_chapter, resolved_chapter = _validated_chapters(db, project_id, row)
    _ensure_source_evidence(row, source_chapter)
    if source_chapter and row.source_chapter_version is None:
        row.source_chapter_version = source_chapter.current_version or 1
    if resolved_chapter:
        row.resolved_chapter_version = resolved_chapter.current_version or 1
    if created:
        db.add(row)
    db.flush()
    if created or previous_status != row.status:
        _record_event(
            db,
            project_id,
            "narrative-debts",
            row.id,
            from_status=previous_status,
            to_status=row.status,
            chapter=resolved_chapter or source_chapter,
            note=row.resolution_note or row.evidence or "叙事债务已登记",
            actor=_clean(data.get("source"), 50) or "candidate",
        )
    return row


def record_character_state(db: Session, project_id: str, data: dict[str, Any]) -> CharacterNarrativeState:
    if not data.get("character_id"):
        raise ValueError("character_id is required")
    character = (
        db.query(Character)
        .filter(Character.id == data["character_id"], Character.project_id == project_id)
        .first()
    )
    if not character:
        raise ValueError("角色不存在或不属于当前作品")
    if data.get("chapter_id") and not _chapter(db, project_id, data["chapter_id"]):
        raise ValueError("角色状态关联章节不存在或不属于当前作品")
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
    chapter = _chapter(db, project_id, data["chapter_id"])
    if not chapter:
        raise ValueError("质量记录关联章节不存在或不属于当前作品")
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
        total_score=float(data["total_score"]) if data.get("total_score") is not None else None,
        max_score=float(data["max_score"]) if data.get("max_score") is not None else None,
        dimension_scores=list(data.get("dimension_scores") or data.get("scores") or []),
        overall_assessment=_clean(data.get("overall_assessment"), 4000) or None,
        model=_clean(data.get("model"), 300) or None,
        chapter_version=(
            int(data["chapter_version"])
            if data.get("chapter_version") is not None
            else (chapter.current_version or 1)
        ),
        source=_clean(data.get("source"), 50) or "manual",
        **values,
    )
    db.add(row)
    db.flush()
    return row


def record_chapter_governance_review(
    db: Session,
    project_id: str,
    chapter: Chapter,
    *,
    source: str,
    findings_count: int,
    evidence: str,
    confidence: float | None = None,
) -> ChapterGovernanceReview:
    """Upsert review proof for the exact chapter revision that was assessed."""

    version = chapter.current_version or 1
    row = (
        db.query(ChapterGovernanceReview)
        .filter(
            ChapterGovernanceReview.project_id == project_id,
            ChapterGovernanceReview.chapter_id == chapter.id,
            ChapterGovernanceReview.chapter_version == version,
        )
        .first()
    )
    if not row:
        row = ChapterGovernanceReview(
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_version=version,
        )
        db.add(row)
    normalized_source = _clean(source, 50) or "fallback"
    normalized_findings = max(0, int(findings_count or 0))
    preserve_verification = row.status == "verified" and row.findings_count == normalized_findings
    row.source = normalized_source
    if not preserve_verification:
        row.status = "needs_review" if normalized_source == "fallback" else "assessed"
        row.reviewed_at = None
    row.findings_count = normalized_findings
    row.confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    if not preserve_verification:
        row.evidence = _clean(evidence, 4000) or "已完成叙事治理覆盖检查"
    db.flush()
    return row


def verify_chapter_governance_review(
    db: Session,
    project_id: str,
    review_id: str,
    *,
    evidence: str,
) -> ChapterGovernanceReview:
    row = (
        db.query(ChapterGovernanceReview)
        .filter(
            ChapterGovernanceReview.id == review_id,
            ChapterGovernanceReview.project_id == project_id,
        )
        .first()
    )
    if not row:
        raise ValueError("治理覆盖记录不存在")
    chapter = _chapter(db, project_id, row.chapter_id)
    if not chapter:
        raise ValueError("治理覆盖记录关联的章节不存在")
    if (chapter.current_version or 1) != row.chapter_version:
        row.status = "stale"
        db.flush()
        raise ValueError("章节内容已经变化，请先重新执行治理检查")
    note = _clean(evidence, 4000)
    if len(note) < 4:
        raise ValueError("确认覆盖前请填写至少 4 个字符的复核说明")
    row.status = "verified"
    row.evidence = note
    row.reviewed_at = datetime.utcnow()
    db.flush()
    return row


def mark_governance_items_stale_for_chapter(
    db: Session,
    project_id: str,
    chapter_id: str,
    *,
    reason: str,
    actor: str = "chapter_change",
) -> int:
    """Invalidate conclusions and coverage tied to a changed chapter revision."""

    chapter = _chapter(db, project_id, chapter_id)
    if not chapter:
        return 0
    changed = 0
    for item_type, model in (
        ("foreshadowings", Foreshadowing),
        ("causal-edges", CausalEdge),
        ("narrative-debts", NarrativeDebt),
    ):
        linked_filters = [
            model.source_chapter_id == chapter_id,
            model.resolved_chapter_id == chapter_id,
        ]
        if hasattr(model, "target_chapter_id"):
            linked_filters.append(model.target_chapter_id == chapter_id)
        rows = (
            db.query(model)
            .filter(
                model.project_id == project_id,
                or_(*linked_filters),
            )
            .all()
        )
        for row in rows:
            if row.status == "stale":
                continue
            previous = row.status
            row.status = "stale"
            row.stale_reason = _clean(reason, 4000) or "关联章节已修改，需要重新复检"
            row.verified_at = None
            row.verification_note = None
            row.closed_by = None
            row.last_checked_at = datetime.utcnow()
            _record_event(
                db,
                project_id,
                item_type,
                row.id,
                from_status=previous,
                to_status="stale",
                chapter=chapter,
                note=row.stale_reason,
                actor=actor,
            )
            changed += 1

    reviews = (
        db.query(ChapterGovernanceReview)
        .filter(
            ChapterGovernanceReview.project_id == project_id,
            ChapterGovernanceReview.chapter_id == chapter_id,
            ChapterGovernanceReview.status != "stale",
        )
        .all()
    )
    for review in reviews:
        review.status = "stale"
        changed += 1
    db.flush()
    return changed


def apply_governance_candidates(db: Session, project_id: str, candidates: list[dict[str, Any]], *, chapter_id: str | None = None) -> list[dict[str, Any]]:
    results = []
    for candidate in candidates:
        item = dict(candidate)
        if not _is_resolution_submission(item):
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


def _governance_entry_payload(value: Any) -> dict[str, Any]:
    item = dict(value) if isinstance(value, dict) else {"title": _clean(value, 500)}
    title = _clean(
        item.get("title")
        or item.get("name")
        or item.get("description")
        or item.get("summary")
        or item.get("promise")
        or item.get("action"),
        500,
    )
    if title:
        item["title"] = title
    return item


def apply_chapter_governance_payload(
    db: Session,
    project_id: str,
    payload: dict[str, Any],
    *,
    chapter_id: str,
) -> dict[str, Any]:
    """Project one cataloging payload into the structured governance tables.

    Cataloging and post-write archiving share the same chapter-summary
    contract.  Keeping this projection here prevents API cataloging from
    updating only the legacy narrative ledger while leaving the governance
    dashboard empty.
    """

    state = normalize_chapter_narrative_state(payload)
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    for raw in state.get("foreshadowing_planted") or []:
        item = _governance_entry_payload(raw)
        if not item.get("title"):
            warnings.append("invalid_governance_candidate")
            continue
        item.update({"type": "foreshadowing", "status": "open"})
        item.setdefault("source", "cataloging")
        candidates.append(item)

    for raw in state.get("foreshadowing_resolved") or []:
        item = _governance_entry_payload(raw)
        if not _has_stable_governance_reference(item):
            warnings.append("unlinked_foreshadowing_resolution")
            continue
        item.update(
            {
                "type": "foreshadowing",
                "status": "fulfilled",
                "resolved_chapter_id": chapter_id,
            }
        )
        item.setdefault("source", "cataloging")
        candidates.append(item)

    for raw in state.get("unresolved_actions") or []:
        item = _governance_entry_payload(raw)
        if not item.get("title"):
            warnings.append("invalid_governance_candidate")
            continue
        item.update(
            {
                "type": "narrative_debt",
                "debt_type": item.get("debt_type") or "unresolved_action",
                "status": "open",
            }
        )
        if item.get("importance") and not item.get("priority"):
            item["priority"] = item["importance"]
        item.setdefault("source", "cataloging")
        candidates.append(item)

    root_candidates = payload.get("governance_candidates")
    if isinstance(root_candidates, list):
        for item in root_candidates:
            if isinstance(item, dict):
                candidates.append(dict(item))
            else:
                warnings.append("invalid_governance_candidate")

    results: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            with db.begin_nested():
                applied = apply_governance_candidates(
                    db,
                    project_id,
                    [candidate],
                    chapter_id=chapter_id,
                )
                if not applied:
                    raise ValueError("不支持的治理候选类型")
                results.extend(applied)
        except (TypeError, ValueError):
            warnings.append("invalid_governance_candidate")

    return {"items": results, "warnings": list(dict.fromkeys(warnings))}


def _recent_events_by_item(db: Session, project_id: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rows = (
        db.query(NarrativeGovernanceEvent)
        .filter(NarrativeGovernanceEvent.project_id == project_id)
        .order_by(NarrativeGovernanceEvent.created_at.desc())
        .limit(500)
        .all()
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.item_type, row.item_id)
        if len(grouped.setdefault(key, [])) < 6:
            grouped[key].append(_serialize(row))
    return grouped


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

    events = _recent_events_by_item(db, project_id)
    source_ids = {
        str(row.source_chapter_id)
        for row in [*foreshadows, *causal_edges, *debts]
        if getattr(row, "source_chapter_id", None)
    }
    source_chapters = {
        row.id: row
        for row in db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.id.in_(source_ids),
        ).all()
    } if source_ids else {}

    def item_payload(row: Any, item_type: str) -> dict[str, Any]:
        payload = _serialize(row)
        payload["recent_events"] = events.get((item_type, row.id), [])
        location = _source_locator_for_row(row, source_chapters.get(row.source_chapter_id))
        if location:
            payload.update(location)
        return payload

    foreshadow_items = [item_payload(row, "foreshadowings") for row in foreshadows if relevant(row)]
    causal_items = [item_payload(row, "causal-edges") for row in causal_edges if relevant(row)]
    debt_items = [item_payload(row, "narrative-debts") for row in debts if relevant(row)]
    states = db.query(CharacterNarrativeState).filter(CharacterNarrativeState.project_id == project_id).order_by(CharacterNarrativeState.created_at.desc()).limit(100).all()
    latest_states: dict[str, dict[str, Any]] = {}
    for row in states:
        latest_states.setdefault(row.character_id, _serialize(row))
    metrics = db.query(ChapterQualityMetric).filter(ChapterQualityMetric.project_id == project_id).order_by(ChapterQualityMetric.created_at.desc()).limit(200).all()
    checkpoints = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.project_id == project_id).order_by(NarrativeCheckpoint.sequence.desc()).limit(30).all()
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
        .all()
    )
    reviews = (
        db.query(ChapterGovernanceReview)
        .filter(ChapterGovernanceReview.project_id == project_id)
        .order_by(ChapterGovernanceReview.created_at.desc())
        .all()
    )
    review_by_revision = {
        (row.chapter_id, row.chapter_version): row
        for row in reviews
    }
    latest_review_by_chapter: dict[str, ChapterGovernanceReview] = {}
    for row in reviews:
        latest_review_by_chapter.setdefault(row.chapter_id, row)
    coverage_items: list[dict[str, Any]] = []
    for chapter in chapters:
        version = chapter.current_version or 1
        review = review_by_revision.get((chapter.id, version))
        if review:
            payload = _serialize(review)
            payload["chapter_title"] = chapter.title
        else:
            previous = latest_review_by_chapter.get(chapter.id)
            previous_was_invalidated = bool(previous and previous.status == "stale")
            payload = {
                "id": None,
                "project_id": project_id,
                "chapter_id": chapter.id,
                "chapter_title": chapter.title,
                "chapter_version": version,
                "status": "stale" if previous_was_invalidated else "missing",
                "source": previous.source if previous_was_invalidated else None,
                "findings_count": previous.findings_count if previous_was_invalidated else 0,
                "confidence": previous.confidence if previous_was_invalidated else None,
                "evidence": (
                    f"正文已从已检查的 v{previous.chapter_version} 发生变化，请重新执行治理检查"
                    if previous_was_invalidated
                    else None
                ),
                "reviewed_at": None,
                "previous_review_version": (
                    previous.chapter_version if previous_was_invalidated else None
                ),
            }
        coverage_items.append(payload)
    coverage_gaps = sum(item["status"] in {"missing", "needs_review", "stale"} for item in coverage_items)

    checkpoint_items = []
    for row in checkpoints:
        payload = {key: value for key, value in _serialize(row).items() if key != "state_json"}
        payload["review_summary"] = (row.state_json or {}).get("_review")
        checkpoint_items.append(payload)
    return {
        "foreshadowings": foreshadow_items,
        "causal_edges": causal_items,
        "narrative_debts": debt_items,
        "character_states": list(latest_states.values()),
        "quality_metrics": [_serialize(row) for row in metrics],
        "chapter_reviews": coverage_items,
        "coverage": {
            "total_chapters": len(chapters),
            "assessed_chapters": sum(item["status"] in {"assessed", "verified"} for item in coverage_items),
            "verified_chapters": sum(item["status"] == "verified" for item in coverage_items),
            "gaps": coverage_gaps,
        },
        "checkpoints": checkpoint_items,
        "counts": {
            "open_foreshadowings": sum(row.status in OPEN_STATUSES for row in foreshadows),
            "open_causal_edges": sum(row.status in OPEN_STATUSES for row in causal_edges),
            "open_debts": sum(row.status in OPEN_STATUSES for row in debts),
            "pending_review": sum(row.status == "pending_review" for row in [*foreshadows, *causal_edges, *debts]),
            "stale": sum(row.status == "stale" for row in [*foreshadows, *causal_edges, *debts]),
            "coverage_gaps": coverage_gaps,
            "high_risk": sum(relevant(row) for row in [*foreshadows, *causal_edges, *debts]) if view == "risk" else None,
        },
    }


def governance_context(db: Session, project_id: str, *, chapter_id: str | None = None, limit: int = 12) -> str:
    dashboard = governance_dashboard(db, project_id, chapter_id=chapter_id or "", view="all")
    items: list[tuple[int, str]] = []
    for row in dashboard["narrative_debts"]:
        if row["status"] in OPEN_STATUSES:
            weight = IMPORTANCE_WEIGHT.get(row.get("priority"), 2)
            items.append((weight + 4, f"[叙事债务/{row['priority']}/ID:{row['id']}] {row['title']}"))
    for row in dashboard["foreshadowings"]:
        if row["status"] in OPEN_STATUSES:
            weight = IMPORTANCE_WEIGHT.get(row.get("importance"), 2)
            due = f"，目标第{row['target_chapter_number']}章" if row.get("target_chapter_number") else ""
            items.append((weight + 3, f"[伏笔/{row['importance']}/ID:{row['id']}] {row['title']}{due}"))
    for row in dashboard["causal_edges"]:
        if row["status"] in OPEN_STATUSES:
            items.append((int(float(row.get("strength") or 0) * 5) + 2, f"[未闭环因果/ID:{row['id']}] {row['cause']} -> {row['effect']}"))
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
        "chapter_reviews": [_serialize(row) for row in db.query(ChapterGovernanceReview).filter(ChapterGovernanceReview.project_id == project_id).all()],
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
    sequence = (db.query(func.max(NarrativeCheckpoint.sequence)).filter(NarrativeCheckpoint.project_id == project_id).scalar() or 0) + 1
    state = _snapshot_state(db, project_id)
    if review_summary:
        state["_review"] = review_summary
    checkpoint = NarrativeCheckpoint(
        project_id=project_id,
        chapter_id=chapter.id if chapter else None,
        chapter_snapshot_id=snapshot_id,
        sequence=sequence,
        label=_clean(label, 300) or (f"{chapter.title} 写后状态" if chapter else f"叙事检查点 {sequence}"),
        trigger_type=trigger_type,
        state_json=state,
    )
    db.add(checkpoint)
    db.flush()
    return checkpoint


def restore_narrative_checkpoint(db: Session, project_id: str, checkpoint_id: str) -> NarrativeCheckpoint:
    checkpoint = db.query(NarrativeCheckpoint).filter(NarrativeCheckpoint.id == checkpoint_id, NarrativeCheckpoint.project_id == project_id).first()
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
        chapter = db.query(Chapter).filter(Chapter.id == checkpoint.chapter_id, Chapter.project_id == project_id).first()
        snapshot = db.query(ChapterSnapshot).filter(ChapterSnapshot.id == checkpoint.chapter_snapshot_id, ChapterSnapshot.chapter_id == checkpoint.chapter_id).first()
        if not chapter or not snapshot:
            raise ValueError("检查点关联的章节版本不存在")
        restore_chapter_from_snapshot(db, chapter, snapshot)
    state = checkpoint.state_json or {}
    for model in (ChapterGovernanceReview, NarrativeDebt, CharacterNarrativeState, ChapterQualityMetric, CausalEdge, Foreshadowing):
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
