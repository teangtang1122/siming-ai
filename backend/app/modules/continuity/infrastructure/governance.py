"""SQLAlchemy narrative governance command implementation."""

from __future__ import annotations

from datetime import datetime

from ....architecture.uow import SqlAlchemyUnitOfWork
from ...story.interfaces.chapter_evidence import ChapterEvidenceReader
from ..domain.governance_lifecycle import normalize_item_type, validate_transition
from .models import CausalEdge, Foreshadowing, NarrativeDebt, NarrativeGovernanceEvent

STATUS_UPDATE_FIELDS = frozenset(
    {
        "status",
        "target_chapter_id",
        "target_chapter_number",
        "resolved_chapter_id",
        "evidence",
        "resolution_note",
        "resolution_evidence",
        "verification_note",
        "closed_by",
    }
)


def apply_governance_status_update(
    session,
    chapter_evidence: ChapterEvidenceReader,
    project_id: str,
    item_type: str,
    item_id: str,
    values: dict,
    *,
    commit: bool,
) -> dict | None:
    """Apply the canonical PC governance lifecycle without duplicating rules.

    The HTTP command uses ``commit=True``. Gateway offline replay uses
    ``commit=False`` so the outer revisioned-sync transaction stays atomic, but
    both paths share transition validation, evidence checks, closure metadata,
    and event recording.
    """

    normalized_type = normalize_item_type(item_type)
    model = {
        "foreshadowings": Foreshadowing,
        "causal-edges": CausalEdge,
        "narrative-debts": NarrativeDebt,
    }[normalized_type]
    row = (
        session.query(model)
        .filter(model.project_id == project_id, model.id == item_id)
        .first()
    )
    if not row:
        return None

    safe_values = {key: value for key, value in values.items() if key in STATUS_UPDATE_FIELDS}
    target_status = str(safe_values.get("status") or row.status)
    validate_transition(normalized_type, row.status, target_status, safe_values)

    resolved_chapter = None
    target_chapter_id = safe_values.get("target_chapter_id")
    if target_chapter_id:
        target_chapter = chapter_evidence.get(
            session,
            project_id=project_id,
            chapter_id=target_chapter_id,
        )
        if not target_chapter:
            raise ValueError("计划处理章节不存在或不属于当前作品")
    resolved_chapter_id = safe_values.get("resolved_chapter_id")
    if resolved_chapter_id:
        resolved_chapter = chapter_evidence.get(
            session,
            project_id=project_id,
            chapter_id=resolved_chapter_id,
        )
        if not resolved_chapter:
            raise ValueError("解决章节不存在或不属于当前作品")

    previous_status = row.status
    now = datetime.utcnow()

    def apply_values() -> None:
        for key, value in safe_values.items():
            if hasattr(row, key):
                setattr(row, key, value)

        row.last_checked_at = now
        if resolved_chapter is not None:
            row.resolved_chapter_version = resolved_chapter["current_version"]
        if target_status in {"fulfilled", "resolved"}:
            row.verified_at = now
            row.closed_by = str(safe_values.get("closed_by") or "user")[:50]
            row.stale_reason = None
        elif target_status in {"abandoned", "invalidated"}:
            row.verified_at = None
            row.closed_by = str(safe_values.get("closed_by") or "user")[:50]
            row.stale_reason = None
        elif target_status == "open":
            row.verified_at = None
            row.verification_note = None
            row.closed_by = None
            row.stale_reason = None

        session.add(
            NarrativeGovernanceEvent(
                project_id=project_id,
                item_type=normalized_type,
                item_id=row.id,
                from_status=previous_status,
                to_status=target_status,
                chapter_id=resolved_chapter["id"] if resolved_chapter else None,
                chapter_version=(
                    resolved_chapter["current_version"]
                    if resolved_chapter
                    else None
                ),
                note=str(
                    safe_values.get("verification_note")
                    or safe_values.get("resolution_evidence")
                    or safe_values.get("resolution_note")
                    or safe_values.get("evidence")
                    or ""
                )[:4000]
                or None,
                actor=str(safe_values.get("closed_by") or "user")[:50],
            )
        )
        session.flush()

    if commit:
        with SqlAlchemyUnitOfWork.from_session(session) as uow:
            apply_values()
            uow.commit()
    else:
        apply_values()

    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class SqlAlchemyNarrativeGovernanceCommands:
    def __init__(self, chapter_evidence: ChapterEvidenceReader) -> None:
        self._chapter_evidence = chapter_evidence

    def update_status(
        self,
        session,
        project_id: str,
        item_type: str,
        item_id: str,
        values: dict,
    ) -> dict | None:
        return apply_governance_status_update(
            session,
            self._chapter_evidence,
            project_id,
            item_type,
            item_id,
            values,
            commit=True,
        )


__all__ = [
    "STATUS_UPDATE_FIELDS",
    "SqlAlchemyNarrativeGovernanceCommands",
    "apply_governance_status_update",
]
