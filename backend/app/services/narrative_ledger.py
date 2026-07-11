"""Narrative ledger projected from cataloging facts.

The ledger deliberately reuses ``CatalogingFact`` as its durable store.  It
adds stable identities and lifecycle status without creating a second source of
truth beside the cataloging pipeline.
"""
from __future__ import annotations

import json
import re
from hashlib import sha1
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingFact, Chapter
from .chapter_service import ensure_current_snapshot


LEDGER_TYPES = {"completed_beat", "revealed_clue", "narrative_promise", "storyline_state"}
LEDGER_FACT_TYPE = "narrative_ledger_entry"
CHECKPOINT_FACT_TYPE = "narrative_ledger_checkpoint"
_NOISE_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_STORYLINE_WORDS = ("storyline", "story line", "plotline", "arc", "故事线", "剧情线", "主线", "支线")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("title") or value.get("name") or value.get("description") or value.get("summary") or ""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_ledger_key(value: Any, *, ledger_type: str, storyline: str = "") -> str:
    text = _text(value).lower()
    if ledger_type == "storyline_state":
        for word in _STORYLINE_WORDS:
            text = text.replace(word, "")
    canonical = _NOISE_RE.sub("", text)[:160] or "untitled"
    scope = _NOISE_RE.sub("", storyline.lower())[:80]
    digest = sha1(f"{ledger_type}|{scope}|{canonical}".encode("utf-8")).hexdigest()[:16]
    return f"nl_{digest}"


def _entry(
    ledger_type: str,
    raw: Any,
    *,
    status: str,
    chapter: Chapter,
    storyline: str = "",
    category: str = "",
) -> dict[str, Any] | None:
    payload = dict(raw) if isinstance(raw, dict) else {"description": _text(raw)}
    title = _text(payload)[:500]
    if not title:
        return None
    resolved_storyline = _text(payload.get("storyline") or payload.get("storyline_title") or storyline)[:200]
    identity = payload.get("ledger_key") or payload.get("key") or payload.get("id") or payload.get("promise_id") or title
    payload.update({
        "ledger_type": ledger_type,
        "ledger_key": normalize_ledger_key(identity, ledger_type=ledger_type, storyline=resolved_storyline),
        "title": title,
        "status": _text(payload.get("status")) or status,
        "storyline": resolved_storyline,
        "category": category or _text(payload.get("category")),
        "first_chapter_id": payload.get("first_chapter_id") or chapter.id,
        "first_chapter_title": payload.get("first_chapter_title") or chapter.title,
        "last_chapter_id": chapter.id,
        "last_chapter_title": chapter.title,
    })
    return payload


def entries_from_narrative_state(chapter: Chapter, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Map the shared narrative-state contract into four lifecycle ledgers."""
    entries: list[dict[str, Any]] = []
    for key in ("events", "timeline_events"):
        entries.extend(filter(None, (_entry("completed_beat", item, status="completed", chapter=chapter) for item in _as_list(state.get(key)))))
    for item in _as_list(state.get("reader_known_facts")):
        entry = _entry("revealed_clue", item, status="active", chapter=chapter)
        if entry:
            entries.append(entry)
    for item in _as_list(state.get("foreshadowing_planted")):
        entry = _entry("narrative_promise", item, status="open", chapter=chapter, category="foreshadowing")
        if entry:
            entries.append(entry)
    for item in _as_list(state.get("foreshadowing_resolved")):
        entry = _entry("narrative_promise", item, status="fulfilled", chapter=chapter, category="foreshadowing")
        if entry:
            entries.append(entry)
    for key in ("storyline_progress", "new_storylines"):
        for item in _as_list(state.get(key)):
            entry = _entry("storyline_state", item, status="active", chapter=chapter)
            if entry:
                entries.append(entry)
    for item in _as_list(state.get("unresolved_actions")):
        entry = _entry("narrative_promise", item, status="open", chapter=chapter, category="unresolved_action")
        if entry:
            entries.append(entry)
    return entries


def record_narrative_ledger(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Persist ledger entries and supersede only matching active identities."""
    created: list[CatalogingFact] = []
    action_counts = {"new": 0, "advanced": 0, "fulfilled": 0, "invalidated": 0, "pending_review": 0}
    for entry in entries_from_narrative_state(chapter, state):
        ledger_type = entry["ledger_type"]
        ledger_key = entry["ledger_key"]
        matches = []
        for fact in (
            db.query(CatalogingFact)
            .filter(CatalogingFact.project_id == chapter.project_id)
            .filter(CatalogingFact.fact_type == LEDGER_FACT_TYPE)
            .filter(CatalogingFact.status == "active")
            .all()
        ):
            try:
                current = json.loads(fact.raw_payload or "{}")
            except json.JSONDecodeError:
                current = {}
            if current.get("ledger_type") == ledger_type and current.get("ledger_key") == ledger_key:
                matches.append(fact)
        for fact in matches:
            fact.status = "superseded"
        if matches:
            entry["first_chapter_id"] = _ledger_value(matches[-1], "first_chapter_id") or entry["first_chapter_id"]
            entry["first_chapter_title"] = _ledger_value(matches[-1], "first_chapter_title") or entry["first_chapter_title"]
            action_counts["advanced"] += 1
        else:
            action_counts["new"] += 1
        if entry.get("status") in {"fulfilled", "resolved"}:
            action_counts["fulfilled"] += 1
        if entry.get("status") in {"invalid", "invalidated"}:
            action_counts["invalidated"] += 1
        confidence = candidate.confidence
        if confidence is not None and confidence < 0.55:
            entry["status"] = "pending_review"
            action_counts["pending_review"] += 1
        fact = CatalogingFact(
            job_id=candidate.job_id,
            chapter_run_id=candidate.chapter_run_id,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            fact_type=LEDGER_FACT_TYPE,
            raw_payload=json.dumps(entry, ensure_ascii=False, sort_keys=True),
            confidence=confidence,
            evidence=candidate.evidence,
            sort_order=candidate.sort_order or 0,
            status="active",
        )
        db.add(fact)
        db.flush()
        created.append(fact)
    return {"items": [ledger_fact_to_dict(fact) for fact in created], "counts": action_counts}


def _ledger_value(fact: CatalogingFact, key: str) -> str:
    try:
        return str(json.loads(fact.raw_payload or "{}").get(key) or "")
    except json.JSONDecodeError:
        return ""


def ledger_fact_to_dict(fact: CatalogingFact) -> dict[str, Any]:
    try:
        payload = json.loads(fact.raw_payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": fact.id,
        "chapter_id": fact.chapter_id,
        "ledger_type": payload.get("ledger_type"),
        "ledger_key": payload.get("ledger_key"),
        "title": payload.get("title"),
        "status": payload.get("status"),
        "storyline": payload.get("storyline"),
        "category": payload.get("category"),
        "first_chapter_id": payload.get("first_chapter_id"),
        "last_chapter_id": payload.get("last_chapter_id"),
        "confidence": fact.confidence,
        "evidence": fact.evidence,
        "fact_status": fact.status,
        "created_at": fact.created_at.isoformat() if fact.created_at else None,
    }


def list_narrative_ledger(db: Session, project_id: str, *, chapter_id: str = "", types: Iterable[str] = (), statuses: Iterable[str] = (), storyline: str = "") -> list[dict[str, Any]]:
    wanted_types = {str(item).strip() for item in types if str(item).strip()} & LEDGER_TYPES
    wanted_statuses = {str(item).strip() for item in statuses if str(item).strip()}
    rows = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.project_id == project_id)
        .filter(CatalogingFact.fact_type == LEDGER_FACT_TYPE)
        .filter(CatalogingFact.status == "active")
        .order_by(CatalogingFact.created_at.desc())
        .all()
    )
    items = []
    for row in rows:
        item = ledger_fact_to_dict(row)
        if chapter_id and item["chapter_id"] != chapter_id:
            continue
        if wanted_types and item.get("ledger_type") not in wanted_types:
            continue
        if wanted_statuses and item.get("status") not in wanted_statuses:
            continue
        if storyline and storyline.lower() not in str(item.get("storyline") or "").lower():
            continue
        items.append(item)
    return items


def revise_narrative_ledger_entry(
    db: Session,
    project_id: str,
    entry_id: str,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    """Create a revised ledger fact so manual changes remain auditable."""
    source = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.id == entry_id, CatalogingFact.project_id == project_id)
        .filter(CatalogingFact.fact_type == LEDGER_FACT_TYPE, CatalogingFact.status == "active")
        .first()
    )
    if not source:
        return None
    try:
        payload = json.loads(source.raw_payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return None
    for key in ("title", "status", "storyline", "category"):
        if key in changes and str(changes[key] or "").strip():
            payload[key] = str(changes[key]).strip()[:500]
    note = str(changes.get("note") or "").strip()
    if note:
        payload["manual_note"] = note[:2000]
    payload["manually_revised"] = True
    source.status = "superseded"
    revised = CatalogingFact(
        job_id=source.job_id,
        chapter_run_id=source.chapter_run_id,
        project_id=source.project_id,
        chapter_id=source.chapter_id,
        fact_type=LEDGER_FACT_TYPE,
        raw_payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        confidence=1.0,
        evidence=note or source.evidence,
        sort_order=source.sort_order,
        status="active",
    )
    db.add(revised)
    db.flush()
    return ledger_fact_to_dict(revised)


def create_ledger_checkpoint(db: Session, run: CatalogingChapterRun, chapter: Chapter) -> dict[str, Any]:
    snapshot = ensure_current_snapshot(db, chapter, "post_write_archive")
    fact_ids = [item["id"] for item in list_narrative_ledger(db, chapter.project_id, chapter_id=chapter.id)]
    checkpoint = CatalogingFact(
        job_id=run.job_id,
        chapter_run_id=run.id,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        fact_type=CHECKPOINT_FACT_TYPE,
        raw_payload=json.dumps({"snapshot_id": snapshot.id, "version_number": snapshot.version_number, "ledger_fact_ids": fact_ids}, ensure_ascii=False),
        sort_order=9999,
        status="active",
    )
    db.add(checkpoint)
    db.flush()
    return {"id": checkpoint.id, "snapshot_id": snapshot.id, "version_number": snapshot.version_number, "ledger_fact_count": len(fact_ids)}


def restore_ledger_checkpoint(db: Session, project_id: str, chapter: Chapter, snapshot_id: str) -> dict[str, Any]:
    checkpoints = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.project_id == project_id, CatalogingFact.chapter_id == chapter.id)
        .filter(CatalogingFact.fact_type == CHECKPOINT_FACT_TYPE, CatalogingFact.status == "active")
        .order_by(CatalogingFact.created_at.desc())
        .all()
    )
    checkpoint = next((row for row in checkpoints if _checkpoint_snapshot_id(row) == snapshot_id), None)
    if not checkpoint:
        return {"ledger_checkpoint_id": None, "restored_count": 0, "conflicts": ["ledger_checkpoint_not_found"]}
    payload = json.loads(checkpoint.raw_payload or "{}")
    selected_ids = set(payload.get("ledger_fact_ids") or [])
    chapter_facts = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.project_id == project_id, CatalogingFact.chapter_id == chapter.id)
        .filter(CatalogingFact.fact_type == LEDGER_FACT_TYPE)
        .all()
    )
    for fact in chapter_facts:
        fact.status = "superseded"
    restored = 0
    conflicts: list[str] = []
    for fact in chapter_facts:
        if fact.id not in selected_ids:
            continue
        key = _ledger_value(fact, "ledger_key")
        ledger_type = _ledger_value(fact, "ledger_type")
        other_active = [
            row for row in db.query(CatalogingFact).filter(
                CatalogingFact.project_id == project_id,
                CatalogingFact.fact_type == LEDGER_FACT_TYPE,
                CatalogingFact.status == "active",
            ).all()
            if row.chapter_id != chapter.id and _ledger_value(row, "ledger_key") == key and _ledger_value(row, "ledger_type") == ledger_type
        ]
        if other_active:
            conflicts.append(f"cross_chapter_ledger_conflict:{ledger_type}:{key}")
            continue
        fact.status = "active"
        restored += 1
    return {"ledger_checkpoint_id": checkpoint.id, "restored_count": restored, "conflicts": conflicts}


def _checkpoint_snapshot_id(fact: CatalogingFact) -> str:
    try:
        return str(json.loads(fact.raw_payload or "{}").get("snapshot_id") or "")
    except json.JSONDecodeError:
        return ""
