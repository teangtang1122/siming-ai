"""Chapter-level cataloging writes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter, ChapterSummary
from ..narrative_governance import (
    apply_chapter_governance_payload,
    record_chapter_governance_review,
)
from ..narrative_ledger import record_narrative_ledger
from ..story_granularity import (
    derive_chapter_summary_text,
    has_chapter_narrative_state,
    narrative_counts,
    normalize_chapter_narrative_state,
)
from .facts import record_cataloging_fact


def _record_governance_coverage(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist review proof for the exact chapter revision being archived."""

    review = payload.get("narrative_review")
    governance_candidates = payload.get("governance_candidates")
    has_contract = (
        isinstance(payload.get("narrative_state"), dict)
        or isinstance(review, dict)
        or isinstance(governance_candidates, list)
    )
    if not has_contract:
        return None

    counts = narrative_counts(payload)
    findings_count = (
        counts["foreshadowing_planted_count"]
        + counts["foreshadowing_resolved_count"]
        + counts["unresolved_action_count"]
        + len(
            [item for item in governance_candidates if isinstance(item, dict)]
            if isinstance(governance_candidates, list)
            else []
        )
    )
    review_payload = review if isinstance(review, dict) else {}
    source = str(review_payload.get("source") or "provided").strip() or "provided"
    raw_confidence = review_payload.get("confidence", candidate.confidence)
    try:
        confidence = float(raw_confidence) if raw_confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    evidence = str(
        review_payload.get("evidence")
        or review_payload.get("reason")
        or candidate.evidence
        or (
            "Cataloging fallback recorded a missing narrative-governance assessment."
            if source == "fallback"
            else f"Cataloging explicitly assessed this chapter revision and found {findings_count} governance item(s)."
        )
    )
    row = record_chapter_governance_review(
        db,
        chapter.project_id,
        chapter,
        source=source,
        findings_count=findings_count,
        evidence=evidence,
        confidence=confidence,
    )
    return {
        "id": row.id,
        "status": row.status,
        "source": row.source,
        "chapter_version": row.chapter_version,
        "findings_count": row.findings_count,
        "confidence": row.confidence,
    }


def apply_chapter_summary(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    summary_text = derive_chapter_summary_text(payload)
    narrative_state = normalize_chapter_narrative_state(payload) if has_chapter_narrative_state(payload) else {}
    if not summary_text and not narrative_state:
        raise ValueError("章节摘要为空")
    governance = apply_chapter_governance_payload(
        db,
        chapter.project_id,
        payload,
        chapter_id=chapter.id,
    )
    governance_review = _record_governance_coverage(db, candidate, chapter, payload)
    if not summary_text and narrative_state:
        narrative_state.setdefault("chapter_id", chapter.id)
        narrative_state.setdefault("chapter_title", chapter.title)
        fact = record_cataloging_fact(
            db,
            candidate,
            chapter,
            fact_type="chapter_narrative_state",
            payload=narrative_state,
        )
        ledger = record_narrative_ledger(db, candidate, chapter, narrative_state)
        return {
            "target_type": "cataloging_fact",
            "target_id": fact.id if fact else None,
            "old_value": None,
            "new_value": {
                **narrative_state,
                "narrative_ledger": ledger,
                "narrative_governance": governance,
                "governance_review": governance_review,
            },
            "detail": "章节叙事状态已归档",
        }
    key_events = payload.get("key_events") if isinstance(payload.get("key_events"), list) else []
    old = None
    summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).first()
    if not summary:
        summary = ChapterSummary(chapter_id=chapter.id, summary_text=summary_text)
        db.add(summary)
    else:
        old = {"summary_text": summary.summary_text, "key_events": summary.key_events}
        summary.summary_text = summary_text
    summary.key_events = json.dumps([str(item) for item in key_events], ensure_ascii=False)
    summary.ai_model = "cataloging"
    summary.updated_at = datetime.utcnow()
    fact = None
    ledger: dict[str, Any] = {"items": [], "counts": {"new": 0, "advanced": 0, "fulfilled": 0, "invalidated": 0, "pending_review": 0}}
    if narrative_state:
        narrative_state.setdefault("chapter_id", chapter.id)
        narrative_state.setdefault("chapter_title", chapter.title)
        fact = record_cataloging_fact(
            db,
            candidate,
            chapter,
            fact_type="chapter_narrative_state",
            payload=narrative_state,
        )
        ledger = record_narrative_ledger(db, candidate, chapter, narrative_state)
    db.flush()
    return {
        "target_type": "chapter_summary",
        "target_id": summary.id,
        "old_value": old,
        "new_value": {
            **payload,
            "narrative_fact_id": fact.id if fact else None,
            "narrative_ledger": ledger,
            "narrative_governance": governance,
            "governance_review": governance_review,
        },
        "detail": "章节摘要已更新",
    }
