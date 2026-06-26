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

_PLACEHOLDER_NAMES = {
    "未命名",
    "未命名角色",
    "未命名主角",
    "未命名设定",
    "未知",
    "无名",
    "角色名",
    "某人",
}

_CHARACTER_STATE_KEYS = {
    "age",
    "life_status",
    "current_location",
    "realm_or_level",
    "physical_state",
    "mental_state",
    "current_goal",
    "active_conflict",
    "abilities_state",
    "items_or_assets",
}

_CHARACTER_DETAIL_KEYS = _CHARACTER_STATE_KEYS | {
    "role_type",
    "appearance",
    "personality",
    "background",
    "abilities",
    "goal",
    "conflict",
    "role_in_scene",
    "aliases",
    "description",
    "summary",
}

_WORLDBUILDING_DETAIL_KEYS = {
    "content",
    "description",
    "event_description",
    "constraints",
    "plot_usage",
    "summary",
}


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


def _clean_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean_value(item) for item in value).strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).strip()
    return str(value or "").strip()


def _has_any_text(payload: dict[str, Any], keys: set[str] | tuple[str, ...]) -> bool:
    return any(_clean_value(payload.get(key)) for key in keys)


def _is_placeholder_name(value: Any) -> bool:
    text = _clean_value(value)
    if not text:
        return True
    normalized = re.sub(r"[\s　:：;；,.，。]+", "", text)
    return normalized in _PLACEHOLDER_NAMES or normalized.startswith("未命名")


def _candidate_identity(normalized: dict[str, Any], *keys: str) -> str:
    payload = normalized.get("payload", {})
    for key in keys:
        value = normalized.get(key)
        if value:
            return _clean_value(value)
        if isinstance(payload, dict) and payload.get(key):
            return _clean_value(payload.get(key))
    return ""


def _skip_reason_for_candidate(normalized: dict[str, Any]) -> str | None:
    item_type = str(normalized.get("item_type") or "")
    payload = normalized.get("payload", {})
    if not isinstance(payload, dict):
        return "候选 payload 不是对象，已跳过"
    evidence = _clean_value(normalized.get("evidence") or payload.get("evidence"))

    if item_type in {"character_create", "character_update", "character_state_update", "character_timeline"}:
        identity = _candidate_identity(normalized, "id", "target_id", "target_name", "name", "character_name")
        if _is_placeholder_name(identity):
            return "角色候选缺少可识别姓名或ID，已跳过，避免生成未命名角色"
        if item_type == "character_state_update" and not _has_any_text(payload, _CHARACTER_STATE_KEYS):
            return f"角色状态候选 {identity} 没有状态字段，已跳过"
        if item_type in {"character_create", "character_update"} and not (
            _has_any_text(payload, _CHARACTER_DETAIL_KEYS) or evidence
        ):
            return f"角色候选 {identity} 只有姓名、没有可写入内容，已跳过"
        if item_type == "character_timeline" and not _clean_value(payload.get("event_description") or payload.get("event")):
            return f"角色时间线候选 {identity} 缺少事件描述，已跳过"

    if item_type == "character_relationship":
        source = _candidate_identity(normalized, "source_name", "source", "from_name", "character_a")
        target = _candidate_identity(normalized, "target_name", "target", "to_name", "character_b")
        if _is_placeholder_name(source) or _is_placeholder_name(target):
            return "关系候选缺少双方角色名，已跳过"
        if not (_clean_value(payload.get("relationship_type")) or _clean_value(payload.get("description")) or evidence):
            return f"关系候选 {source}-{target} 缺少关系内容，已跳过"

    if item_type in {"worldbuilding_create", "worldbuilding_update", "worldbuilding_timeline"}:
        title = _candidate_identity(normalized, "id", "target_id", "target_name", "title", "entry_title")
        if _is_placeholder_name(title):
            return "世界观候选缺少标题或ID，已跳过，避免生成未命名设定"
        if item_type == "worldbuilding_timeline":
            if not _clean_value(payload.get("event_description") or payload.get("event") or payload.get("description")):
                return f"世界观时间线候选 {title} 缺少事件描述，已跳过"
        elif not (_has_any_text(payload, _WORLDBUILDING_DETAIL_KEYS) or evidence):
            return f"世界观候选 {title} 没有内容，已跳过"

    if item_type == "chapter_summary":
        if not _clean_value(payload.get("summary_text") or payload.get("summary") or payload.get("content")):
            return "章节摘要候选为空，已跳过"

    if item_type in {"outline_create", "outline_update"}:
        title = _candidate_identity(normalized, "target_name", "title", "chapter_title", "outline_title")
        if _is_placeholder_name(title):
            return "大纲候选缺少标题，已跳过"
        if not (_clean_value(payload.get("summary")) or _clean_value(payload.get("description")) or _clean_value(payload.get("purpose"))):
            return f"大纲候选 {title} 缺少摘要/作用，已跳过"

    return None


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
    skip_reason = _skip_reason_for_candidate(normalized)
    if skip_reason:
        return {"skipped": True, "reason": skip_reason}
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
    payload_keys = ", ".join(sorted(str(key) for key in normalized.get("payload", {}).keys())[:12])
    raw_keys = ", ".join(sorted(str(key) for key in raw.keys())[:12])
    snippet = json.dumps(raw, ensure_ascii=False, default=str)[:240]
    if raw_type:
        return f"未知 type: {raw_type}（raw_fields: {raw_keys or 'none'}, payload_fields: {payload_keys or 'none'}）"
    return (
        "未知 type: <empty>，无法从字段推断候选类型"
        f"（raw_fields: {raw_keys or 'none'}, payload_fields: {payload_keys or 'none'}, snippet: {snippet}）"
    )
