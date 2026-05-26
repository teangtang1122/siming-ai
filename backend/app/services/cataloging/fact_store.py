"""Persist and reload first-stage cataloging facts."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingFact, CatalogingChapterRun, CatalogingJob
from .candidate_io import float_or_none


def create_fact(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    fact: dict[str, Any],
    sort_order: int,
) -> CatalogingFact:
    payload = fact.get("payload") if isinstance(fact.get("payload"), dict) else {}
    row = CatalogingFact(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=job.project_id,
        chapter_id=run.chapter_id,
        fact_type=str(fact.get("fact_type") or "")[:50],
        raw_payload=json.dumps(payload, ensure_ascii=False),
        confidence=float_or_none(fact.get("confidence")),
        evidence=str(fact.get("evidence") or "")[:2000] or None,
        sort_order=sort_order,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def load_facts_for_run(db: Session, run: CatalogingChapterRun) -> list[dict[str, Any]]:
    rows = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.chapter_run_id == run.id, CatalogingFact.status == "active")
        .order_by(CatalogingFact.sort_order.asc(), CatalogingFact.created_at.asc())
        .all()
    )
    facts: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.raw_payload)
        except Exception:
            payload = {}
        facts.append({
            "fact_type": row.fact_type,
            "confidence": row.confidence,
            "evidence": row.evidence,
            "payload": payload if isinstance(payload, dict) else {},
        })
    return facts


def fact_to_dict(row: CatalogingFact) -> dict[str, Any]:
    try:
        payload = json.loads(row.raw_payload)
    except Exception:
        payload = {}
    return {
        "id": row.id,
        "job_id": row.job_id,
        "chapter_run_id": row.chapter_run_id,
        "chapter_id": row.chapter_id,
        "fact_type": row.fact_type,
        "payload": payload if isinstance(payload, dict) else {},
        "confidence": row.confidence,
        "evidence": row.evidence,
        "sort_order": row.sort_order,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def clear_candidates_for_run(db: Session, run: CatalogingChapterRun) -> None:
    from ...database.models import CatalogingApplyLog, CatalogingCandidate

    candidate_ids = [
        item.id for item in db.query(CatalogingCandidate.id).filter(CatalogingCandidate.chapter_run_id == run.id).all()
    ]
    if candidate_ids:
        db.query(CatalogingApplyLog).filter(CatalogingApplyLog.candidate_id.in_(candidate_ids)).delete(synchronize_session=False)
        db.query(CatalogingCandidate).filter(CatalogingCandidate.id.in_(candidate_ids)).delete(synchronize_session=False)
