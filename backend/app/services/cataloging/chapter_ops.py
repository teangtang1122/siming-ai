"""Chapter-level cataloging writes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter, ChapterSummary
from ..story_granularity import has_chapter_narrative_state, normalize_chapter_narrative_state
from ..narrative_ledger import record_narrative_ledger
from .facts import record_cataloging_fact


def apply_chapter_summary(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    summary_text = str(payload.get("summary_text") or payload.get("summary") or "").strip()
    narrative_state = normalize_chapter_narrative_state(payload) if has_chapter_narrative_state(payload) else {}
    if not summary_text and not narrative_state:
        raise ValueError("章节摘要为空")
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
            "new_value": {**narrative_state, "narrative_ledger": ledger},
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
    return {
        "target_type": "chapter_summary",
        "target_id": summary.id,
        "old_value": old,
        "new_value": {**payload, "narrative_fact_id": fact.id if fact else None, "narrative_ledger": ledger},
        "detail": "章节摘要已更新",
    }
