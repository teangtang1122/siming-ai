"""Create cataloging candidates from streamed model lines."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, CatalogingChapterRun, CatalogingJob
from .candidate_io import float_or_none
from .constants import VALID_ITEM_TYPES
from .jsonl import clean_jsonl_text, normalize_candidate, parse_json_line


_SIGNATURE_PAYLOAD_KEYS = (
    "dimension",
    "title",
    "name",
    "source_name",
    "target_name",
    "primary_name",
    "secondary_name",
    "relationship_type",
    "summary_text",
    "event",
    "event_description",
    "description",
    "content",
    "evidence",
)


def _signature_text(value: Any) -> str:
    if isinstance(value, list):
        text = " ".join(_signature_text(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    return re.sub(r"\s+", "", text).strip().lower()


def _candidate_signature(
    *,
    item_type: str,
    target_name: str | None,
    payload: dict[str, Any],
    evidence: str | None,
) -> str:
    parts = [item_type]
    target = _signature_text(target_name)
    if target:
        parts.append(f"target:{target[:120]}")
    for key in _SIGNATURE_PAYLOAD_KEYS:
        value = _signature_text(payload.get(key))
        if value:
            parts.append(f"{key}:{value[:240]}")
    ev = _signature_text(evidence)
    if ev:
        parts.append(f"evidence:{ev[:240]}")
    if len(parts) == 1:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[:800])
    return "|".join(parts)


def _payload_from_candidate(candidate: CatalogingCandidate) -> dict[str, Any]:
    try:
        parsed = json.loads(candidate.edited_payload or candidate.raw_payload or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _is_duplicate_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> bool:
    item_type = normalized["item_type"]
    signature = _candidate_signature(
        item_type=item_type,
        target_name=str(normalized.get("target_name") or "") or None,
        payload=normalized["payload"],
        evidence=str(normalized.get("evidence") or "") or None,
    )
    query = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.project_id == job.project_id,
        CatalogingCandidate.chapter_id == run.chapter_id,
        CatalogingCandidate.item_type == item_type,
    )
    if item_type == "chapter_summary":
        query = query.filter(CatalogingCandidate.chapter_run_id == run.id)
    for existing in query.all():
        existing_signature = _candidate_signature(
            item_type=existing.item_type,
            target_name=existing.target_name,
            payload=_payload_from_candidate(existing),
            evidence=existing.evidence,
        )
        if existing_signature == signature:
            return True
    return False


def try_create_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    line: str,
    sort_order: int,
) -> dict[str, Any]:
    text = clean_jsonl_text(line)
    if not text:
        return {}
    try:
        parsed = parse_json_line(text)
        if parsed is None:
            return {}
        return create_candidate_from_raw(db, job, run, parsed, sort_order)
    except Exception as exc:
        return {"bad_line": text, "error": str(exc)}


def create_candidate_from_raw(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    sort_order: int,
    *,
    source_task: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_candidate(raw)
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return {
            "bad_line": json.dumps(raw, ensure_ascii=False),
            "error": _unknown_type_message(raw, normalized),
        }
    if _is_duplicate_candidate(db, job, run, normalized):
        return {"duplicate": True}
    candidate = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=job.project_id,
        chapter_id=run.chapter_id,
        item_type=normalized["item_type"],
        operation=normalized["operation"],
        target_type=normalized.get("target_type"),
        target_id=normalized.get("target_id"),
        target_name=str(normalized.get("target_name") or "")[:200] or None,
        raw_payload=json.dumps(normalized["payload"], ensure_ascii=False),
        status="pending",
        confidence=float_or_none(normalized.get("confidence")),
        evidence=str(normalized.get("evidence") or "")[:2000] or None,
        sort_order=sort_order,
        source_task=source_task or normalized.get("source_task"),
    )
    db.add(candidate)
    db.flush()
    return {"candidate": candidate}


def _unknown_type_message(raw: dict[str, Any], normalized: dict[str, Any]) -> str:
    raw_type = (
        raw.get("type")
        or raw.get("item_type")
        or raw.get("candidate_type")
        or raw.get("kind")
        or raw.get("card_type")
        or ""
    )
    keys = ", ".join(sorted(str(key) for key in normalized.get("payload", {}).keys())[:12])
    if raw_type:
        return f"未知 type: {raw_type}"
    return f"未知 type: <empty>，无法从字段推断候选类型（字段: {keys or 'none'}）"
