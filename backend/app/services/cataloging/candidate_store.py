"""Create cataloging candidates from streamed model lines."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    WorldbuildingEntry,
)
from ...database.query_filters import (
    current_worldbuilding_clause,
    is_current_worldbuilding_status,
)
from ...modules.continuity.domain.cataloging_contract import (
    CHAPTER_LINK_REPLACE_FIELDS,
    CHAPTER_LINK_REPLACE_LIST_FIELDS,
    validate_coverage_manifest_relationships,
)
from ..story_granularity import (
    CHARACTER_STABLE_FIELDS,
    CHARACTER_STATE_FIELDS,
    NARRATIVE_STATE_FIELDS,
    has_chapter_narrative_state,
)
from .candidate_io import float_or_none
from .fact_store import load_facts_for_run
from .candidate_validation import (
    inspect_candidate_coverage,
    validate_candidate_source_character_grounding,
)
from .character_targets import (
    validate_character_profile_target,
    validate_character_state_target,
)
from .constants import VALID_ITEM_TYPES
from .jsonl import (
    candidate_response_attempts,
    clean_jsonl_text,
    expand_candidate_records,
    normalize_candidate,
    parse_candidate_response_records,
    parse_json_line,
)
from .repair_identity import has_stable_profile_evidence, is_anonymous_character
from .targeted_context import worldbuilding_identity_review_candidates
from ..character_role_types import normalize_character_role_type

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

_CHARACTER_STATE_KEYS = set(CHARACTER_STATE_FIELDS)

_CHARACTER_DETAIL_KEYS = _CHARACTER_STATE_KEYS | (set(CHARACTER_STABLE_FIELDS) - {"name"})

_WORLDBUILDING_DETAIL_KEYS = {
    "content",
    "description",
    "event_description",
    "constraints",
    "plot_usage",
    "summary",
}

_WORLDBUILDING_IDENTITY_KEYS = {
    "id",
    "title",
    "entry_title",
    "source_fact_titles",
    "item_type",
    "operation",
    "type",
    "action",
}


def _normalize_character_role_payload(normalized: dict[str, Any]) -> None:
    if normalized.get("item_type") not in {"character_create", "character_update"}:
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return
    if payload.get("role_type") not in (None, ""):
        raw_role = payload.get("role_type")
        payload["role_type"] = normalize_character_role_type(raw_role)


def _ensure_narrative_assessment_contract(
    normalized: dict[str, Any],
    *,
    source_task: str | None,
) -> None:
    """Make a missing assessment explicit without pretending the model ran it.

    Older/API-free agents only returned a summary and an outline.  Treating that
    omission as "no narrative issues" created a silent coverage hole, while
    rejecting the whole chapter made existing cataloging workflows unusable.
    Persist an empty state plus a fallback review instead: the archive can be
    applied, but the exact chapter revision remains visibly ``needs_review``.
    """

    if normalized.get("item_type") != "chapter_summary":
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return
    has_assessment = (
        isinstance(payload.get("narrative_state"), dict)
        or isinstance(payload.get("narrative_review"), dict)
        or isinstance(payload.get("governance_candidates"), list)
    )
    if has_assessment:
        return
    payload["narrative_state"] = {key: [] for key in NARRATIVE_STATE_FIELDS}
    payload["narrative_review"] = {
        "source": "fallback",
        "outcome": "assessment_missing",
        "requires_human_review": True,
        "evidence": (
            "The cataloging source did not provide a narrative-governance "
            "assessment; this chapter revision requires review."
        ),
        "source_task": source_task or "cataloging",
    }


def _ensure_outline_identity(
    normalized: dict[str, Any],
    run: CatalogingChapterRun,
) -> None:
    """Recover a missing chapter-outline title from the chapter being filed."""

    if normalized.get("item_type") not in {"outline_create", "outline_update"}:
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict) or _clean_value(payload.get("title")):
        return
    chapter = run.chapter
    if not chapter:
        return
    node_type = str(payload.get("node_type") or "chapter").strip().lower()
    if node_type == "chapter":
        payload["title"] = chapter.title
        normalized["target_name"] = normalized.get("target_name") or chapter.title
    elif node_type in {"section", "scene"} and payload.get("scene_number") is not None:
        title = f"{chapter.title} / 场景{payload['scene_number']}"
        payload["title"] = title
        payload.setdefault("parent_title", chapter.title)
        normalized["target_name"] = normalized.get("target_name") or title


def ensure_outline_section_scene_number(
    db: Session,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> None:
    """Give every staged section a stable, positive number before identity matching.

    Cataloging providers sometimes return ordered section cards without the
    redundant ``scene_number`` field. The order of those cards is already a
    deterministic protocol fact, so persistence can fill the omitted ordinal
    without interpreting prose. Existing editable rows are normalized first
    so retries reuse their run-local identity instead of creating new cards.
    """

    if normalized.get("item_type") not in {"outline_create", "outline_update"}:
        return
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return
    node_type = str(payload.get("node_type") or "chapter").strip().lower()
    if node_type not in {"section", "scene"}:
        return

    rows = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.item_type.in_(("outline_create", "outline_update")),
            CatalogingCandidate.status != "rejected",
        )
        .order_by(
            CatalogingCandidate.sort_order.asc(),
            CatalogingCandidate.created_at.asc(),
            CatalogingCandidate.id.asc(),
        )
        .all()
    )
    section_rows: list[tuple[CatalogingCandidate, dict[str, Any]]] = []
    used_numbers: set[int] = set()
    for row in rows:
        existing_payload = _payload_from_candidate(row)
        existing_type = str(
            existing_payload.get("node_type") or "chapter"
        ).strip().lower()
        if existing_type not in {"section", "scene"}:
            continue
        section_rows.append((row, existing_payload))
        existing_number = _positive_int(existing_payload.get("scene_number"))
        if existing_number:
            used_numbers.add(existing_number)

    next_number = 1
    for row, existing_payload in section_rows:
        if _positive_int(existing_payload.get("scene_number")):
            continue
        if row.status in {"applying", "applied"}:
            continue
        while next_number in used_numbers:
            next_number += 1
        existing_payload["scene_number"] = next_number
        encoded = json.dumps(existing_payload, ensure_ascii=False)
        if row.edited_payload is not None:
            row.edited_payload = encoded
        else:
            row.raw_payload = encoded
        used_numbers.add(next_number)
        next_number += 1

    incoming_number = _positive_int(payload.get("scene_number"))
    if incoming_number:
        payload["scene_number"] = incoming_number
        return

    incoming_title = _signature_text(
        payload.get("title") or normalized.get("target_name")
    )
    if incoming_title:
        for row, existing_payload in section_rows:
            existing_title = _signature_text(
                existing_payload.get("title") or row.target_name
            )
            existing_number = _positive_int(existing_payload.get("scene_number"))
            if existing_title == incoming_title and existing_number:
                payload["scene_number"] = existing_number
                return

    while next_number in used_numbers:
        next_number += 1
    payload["scene_number"] = next_number


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


def _run_local_candidate_identity(normalized: dict[str, Any]) -> str:
    """Return the deterministic one-per-run identity for replaceable cards.

    Provider retries may rewrite descriptive text or titles while referring to
    the same structured entity.  Those presentation changes must update the
    staged candidate instead of creating a second projection row.
    """

    item_type = str(normalized.get("item_type") or "")
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return ""
    if item_type == "chapter_summary":
        return "chapter_summary"
    if item_type == "chapter_link":
        source = _candidate_identity(
            normalized,
            "source_id",
            "source_name",
            "source",
        )
        target = _candidate_identity(
            normalized,
            "target_id",
            "target_name",
            "target",
        )
        source_type = _clean_value(payload.get("source_type"))
        target_type = _clean_value(payload.get("target_type"))
        if source or target or source_type or target_type:
            return ":".join((
                "chapter_link",
                _signature_text(source_type),
                _signature_text(source),
                _signature_text(target_type),
                _signature_text(target),
            ))
        return "chapter_link:aggregate"
    if item_type in {"outline_create", "outline_update"}:
        node_type = str(payload.get("node_type") or "chapter").strip().lower()
        if node_type in {"section", "scene"}:
            scene_number = _positive_int(payload.get("scene_number"))
            return f"outline:section:{scene_number}" if scene_number else ""
        return f"outline:{node_type}"
    if item_type in {"character_create", "character_update"}:
        identity = _candidate_identity(
            normalized,
            "name",
            "character_name",
            "target_name",
            "id",
            "target_id",
        )
        return f"character_profile:{_signature_text(identity)}" if identity else ""
    if item_type == "character_state_update":
        identity = _candidate_identity(
            normalized,
            "name",
            "character_name",
            "target_name",
            "id",
            "target_id",
        )
        return f"character_state:{_signature_text(identity)}" if identity else ""
    if item_type == "character_relationship":
        source = _candidate_identity(
            normalized,
            "source_name",
            "source",
            "from_name",
            "character_a",
        )
        target = _candidate_identity(
            normalized,
            "target_name",
            "target",
            "to_name",
            "character_b",
        )
        if source and target:
            return (
                "character_relationship:"
                f"{_signature_text(source)}:{_signature_text(target)}"
            )
    return ""


def _run_local_identity_item_types(item_type: str) -> tuple[str, ...]:
    if item_type in {"outline_create", "outline_update"}:
        return ("outline_create", "outline_update")
    if item_type in {"character_create", "character_update"}:
        return ("character_create", "character_update")
    return (item_type,)


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
        if (
            item_type in {"character_create", "character_update"}
            and is_anonymous_character(identity)
            and not has_stable_profile_evidence(payload)
        ):
            return "身份未确认且缺少稳定档案，已保留为章节线索"
        if item_type == "character_state_update" and not _has_any_text(payload, _CHARACTER_STATE_KEYS):
            return f"角色状态候选 {identity} 没有状态字段，已跳过"
        if item_type in {"character_create", "character_update"} and not _has_any_text(
            payload, _CHARACTER_DETAIL_KEYS
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
        if item_type == "worldbuilding_update" and not _clean_value(
            normalized.get("target_id") or payload.get("id")
        ):
            return "世界观更新缺少已有条目的精确 ID，已跳过，避免按近义标题创建重复条目"
        title = _candidate_identity(normalized, "id", "target_id", "target_name", "title", "entry_title")
        if _is_placeholder_name(title):
            return "世界观候选缺少标题或ID，已跳过，避免生成未命名设定"
        if item_type == "worldbuilding_timeline":
            if not _clean_value(payload.get("event_description") or payload.get("event") or payload.get("description")):
                return f"世界观时间线候选 {title} 缺少事件描述，已跳过"
        elif not (_has_any_text(payload, _WORLDBUILDING_DETAIL_KEYS) or evidence):
            return f"世界观候选 {title} 没有内容，已跳过"

    if item_type == "chapter_summary":
        if (
            str(payload.get("coverage_manifest_mode") or "").strip().lower()
            == "replace"
            and isinstance(payload.get("coverage_manifest"), dict)
        ):
            return None
        if not _clean_value(payload.get("summary_text") or payload.get("summary") or payload.get("content")) and not has_chapter_narrative_state(payload):
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


def _worldbuilding_body_signature(payload: dict[str, Any]) -> str:
    """Return an exact, non-semantic signature excluding only identity fields."""

    body = {
        key: value
        for key, value in payload.items()
        if key not in _WORLDBUILDING_IDENTITY_KEYS
    }
    if not _has_any_text(body, _WORLDBUILDING_DETAIL_KEYS):
        return ""
    return json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _validate_worldbuilding_source_fact_titles(
    normalized: dict[str, Any],
) -> str | None:
    payload = normalized.get("payload")
    if not isinstance(payload, dict) or "source_fact_titles" not in payload:
        return None
    if normalized.get("item_type") not in {
        "worldbuilding_create",
        "worldbuilding_update",
        "worldbuilding_timeline",
    }:
        return "source_fact_titles 只能用于世界观候选"
    values = payload.get("source_fact_titles")
    if (
        not isinstance(values, list)
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        return "source_fact_titles 必须是非空字符串数组"
    payload["source_fact_titles"] = list(
        dict.fromkeys(value.strip() for value in values)
    )
    return None


def _matching_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> CatalogingCandidate | None:
    item_type = normalized["item_type"]
    if item_type in {"worldbuilding_create", "worldbuilding_update"}:
        family_query = db.query(CatalogingCandidate).filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.item_type.in_(
                ("worldbuilding_create", "worldbuilding_update")
            ),
            CatalogingCandidate.status != "rejected",
        )
        incoming_payload = normalized["payload"]
        incoming_id = _clean_value(
            normalized.get("target_id") or incoming_payload.get("id")
        )
        incoming_title = _signature_text(
            incoming_payload.get("title")
            or incoming_payload.get("entry_title")
            or normalized.get("target_name")
        )
        incoming_body = _worldbuilding_body_signature(incoming_payload)
        rows = family_query.order_by(CatalogingCandidate.sort_order.asc()).all()
        if incoming_id:
            for existing in rows:
                existing_payload = _payload_from_candidate(existing)
                existing_id = _clean_value(
                    existing.target_id or existing_payload.get("id")
                )
                if existing_id and existing_id == incoming_id:
                    return existing
        if incoming_title:
            for existing in rows:
                existing_payload = _payload_from_candidate(existing)
                existing_id = _clean_value(
                    existing.target_id or existing_payload.get("id")
                )
                if incoming_id and existing_id and incoming_id != existing_id:
                    continue
                existing_title = _signature_text(
                    existing_payload.get("title")
                    or existing_payload.get("entry_title")
                    or existing.target_name
                )
                if existing_title == incoming_title:
                    return existing
        if incoming_body:
            for existing in rows:
                existing_payload = _payload_from_candidate(existing)
                existing_id = _clean_value(
                    existing.target_id or existing_payload.get("id")
                )
                if incoming_id and existing_id and incoming_id != existing_id:
                    continue
                if _worldbuilding_body_signature(existing_payload) == incoming_body:
                    return existing
    run_identity = _run_local_candidate_identity(normalized)
    if run_identity:
        identity_rows = (
            db.query(CatalogingCandidate)
            .filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type.in_(
                    _run_local_identity_item_types(item_type)
                ),
                CatalogingCandidate.status != "rejected",
            )
            .order_by(CatalogingCandidate.sort_order.asc())
            .all()
        )
        for existing in identity_rows:
            existing_identity = _run_local_candidate_identity({
                "item_type": existing.item_type,
                "target_id": existing.target_id,
                "target_name": existing.target_name,
                "payload": _payload_from_candidate(existing),
            })
            if existing_identity == run_identity:
                return existing
    signature = _candidate_signature(
        item_type=item_type,
        target_name=str(normalized.get("target_name") or "") or None,
        payload=normalized["payload"],
        evidence=str(normalized.get("evidence") or "") or None,
    )
    # Candidates are review artifacts owned by one cataloging run.  A card
    # produced by an older run must not suppress the same card in a retry or a
    # later re-cataloging job; entity-level upsert/deduplication happens in the
    # applier.  Keeping this scope run-local also lets completeness validation
    # see every required card in the current run.
    query = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.item_type == item_type,
        CatalogingCandidate.status != "rejected",
    )
    for existing in query.all():
        existing_signature = _candidate_signature(
            item_type=existing.item_type,
            target_name=existing.target_name,
            payload=_payload_from_candidate(existing),
            evidence=existing.evidence,
        )
        if existing_signature == signature:
            return existing
    # A chapter has exactly one summary card.  A later call often repairs a
    # partial first attempt by adding the coverage manifest or governance
    # assessment while keeping the same summary text.  Treat that as an
    # idempotent upgrade instead of making the incomplete card impossible to
    # correct.
    if item_type == "chapter_summary":
        return query.order_by(CatalogingCandidate.sort_order.asc()).first()
    # One cataloging run owns exactly one chapter-level outline. Incremental
    # model repairs upgrade that staged card instead of creating duplicates.
    if item_type in {"outline_create", "outline_update"}:
        node_type = str(normalized["payload"].get("node_type") or "chapter").lower()
        if node_type == "chapter":
            outline_query = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type.in_(("outline_create", "outline_update")),
                CatalogingCandidate.status != "rejected",
            )
            for existing in outline_query.order_by(CatalogingCandidate.sort_order.asc()).all():
                existing_payload = _payload_from_candidate(existing)
                if str(existing_payload.get("node_type") or "chapter").lower() == "chapter":
                    return existing
    return None


def _validate_worldbuilding_existing_target(
    db: Session,
    project_id: str,
    normalized: dict[str, Any],
) -> str | None:
    item_type = str(normalized.get("item_type") or "")
    if item_type not in {"worldbuilding_update", "worldbuilding_timeline"}:
        return None
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return "世界观候选 payload 不是对象"
    target_id = _clean_value(normalized.get("target_id") or payload.get("id"))
    if not target_id:
        if item_type == "worldbuilding_update":
            return "世界观更新缺少已有条目的精确 ID"
        return None
    entry = db.get(WorldbuildingEntry, target_id)
    if entry is None or entry.project_id != project_id:
        return "世界观目标 ID 不存在或不属于当前作品"
    if not is_current_worldbuilding_status(entry.status):
        return (
            "世界观目标 ID 已停用，不能作为建档候选或被重新激活；"
            "请从 active worldbuilding_title_index 选择当前条目"
        )
    payload["id"] = entry.id
    normalized["target_id"] = entry.id
    return None


def _validate_worldbuilding_create_identity_review(
    db: Session,
    project_id: str,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> str | None:
    """Require the model to make the new-vs-existing semantic decision explicit.

    The application validates only structured IDs and ownership.  It does not
    guess whether two natural-language titles mean the same thing.
    """

    if normalized.get("item_type") != "worldbuilding_create":
        return None
    active_ids = {
        str(identity)
        for (identity,) in db.query(WorldbuildingEntry.id)
        .filter(
            WorldbuildingEntry.project_id == project_id,
            current_worldbuilding_clause(WorldbuildingEntry.status),
        )
        .all()
    }
    if not active_ids:
        return None
    payload = normalized.get("payload")
    if not isinstance(payload, dict):
        return "世界观新建 payload 不是对象"
    review = payload.get("identity_resolution")
    if not isinstance(review, dict):
        return (
            "世界观新建缺少 identity_resolution；请由模型对照 worldbuilding_title_index，"
            "决定应更新哪个真实 ID 还是确属新设定"
        )
    if str(review.get("decision") or "").strip() != "create":
        return "世界观新建的 identity_resolution.decision 必须为 create；若命中旧设定请改用 worldbuilding_update"
    reviewed = review.get("reviewed_existing_ids")
    if (
        not isinstance(reviewed, list)
        or not reviewed
        or any(not isinstance(value, str) or not value.strip() for value in reviewed)
    ):
        return "世界观新建必须列出模型已比较的 reviewed_existing_ids"
    reviewed_ids = list(dict.fromkeys(value.strip() for value in reviewed))
    invalid = [identity for identity in reviewed_ids if identity not in active_ids]
    if invalid:
        return "世界观新建比较的 ID 不存在、不属于当前作品或已停用：" + "、".join(invalid)
    required_rows = worldbuilding_identity_review_candidates(
        db,
        project_id,
        load_facts_for_run(db, run),
    )
    missing_required = [
        row for row in required_rows if str(row.id) not in set(reviewed_ids)
    ]
    if missing_required:
        return (
            "世界观新建的 identity_resolution.reviewed_existing_ids 未覆盖本章已交付的"
            "相关 active 候选："
            + "、".join(f"{row.id}（{row.title}）" for row in missing_required)
        )
    reason = str(review.get("reason") or "").strip()
    if not reason:
        return "世界观新建必须说明与已比较条目不同、因此确需新建的 reason"
    payload["identity_resolution"] = {
        "decision": "create",
        "reviewed_existing_ids": reviewed_ids,
        "reason": reason,
    }
    return None


def _merge_unique_values(existing: list[Any], incoming: list[Any]) -> list[Any]:
    merged = list(existing)
    signatures = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in merged
    }
    for item in incoming:
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if signature not in signatures:
            merged.append(item)
            signatures.add(signature)
    return merged


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _merge_coverage_manifest(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Only add to an accepted manifest; a retry cannot shrink its contract."""

    merged = dict(existing)
    for key, value in incoming.items():
        old_value = merged.get(key)
        if key == "scene_count":
            merged[key] = max(_positive_int(old_value), _positive_int(value)) or value
        elif isinstance(old_value, list) and isinstance(value, list):
            merged[key] = _merge_unique_values(old_value, value)
        elif key not in merged or old_value in (None, "", [], {}):
            merged[key] = value
    return merged


def _merge_candidate_payload(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    item_type: str = "",
) -> dict[str, Any]:
    coverage_manifest_mode = (
        str(incoming.get("coverage_manifest_mode") or "").strip().lower()
        if item_type == "chapter_summary"
        else ""
    )
    if coverage_manifest_mode == "replace":
        # A managed retry may discover that the first summary listed two names
        # for one logical entity. Treat the explicit replacement as a narrow
        # control operation: keep the accepted prose and narrative ledger, and
        # replace only the complete manifest. The workspace tool validates the
        # full shape and source scene count before this reaches the store.
        merged = dict(existing)
        old_manifest = existing.get("coverage_manifest")
        new_manifest = incoming.get("coverage_manifest")
        if isinstance(new_manifest, dict):
            replacement = dict(new_manifest)
            if isinstance(old_manifest, dict):
                old_scene_count = _positive_int(old_manifest.get("scene_count"))
                new_scene_count = _positive_int(replacement.get("scene_count"))
                if old_scene_count or new_scene_count:
                    replacement["scene_count"] = max(old_scene_count, new_scene_count)
            merged["coverage_manifest"] = replacement
        merged.pop("coverage_manifest_mode", None)
        return merged

    chapter_link_mode = (
        str(incoming.get("chapter_link_mode") or "").strip().lower()
        if item_type == "chapter_link"
        else ""
    )
    if chapter_link_mode == "replace":
        # The workspace tool accepts this only for a one-record managed repair
        # containing every aggregate collection. Clear all identity-bearing
        # link fields first so an earlier alias or wrong endpoint cannot remain
        # active after the model explicitly corrects the record.
        if not all(key in incoming for key in CHAPTER_LINK_REPLACE_LIST_FIELDS):
            raise ValueError(
                "chapter_link replacement requires all aggregate list fields"
            )
        merged = dict(existing)
        for key in CHAPTER_LINK_REPLACE_FIELDS:
            merged.pop(key, None)
        for key, value in incoming.items():
            if key != "chapter_link_mode":
                merged[key] = value
        merged.pop("chapter_link_mode", None)
        return merged

    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_candidate_payload(merged[key], value)
            continue
        if (
            item_type == "chapter_link"
            and isinstance(value, list)
            and isinstance(merged.get(key), list)
        ):
            # One run owns one aggregate chapter link, but managed CLI turns
            # send candidates in small batches.  A later repair must be able
            # to add identities that the completeness gate reports missing
            # without creating a second link or erasing fields already staged.
            merged[key] = _merge_unique_values(merged[key], value)
            continue
        # Explicit empty arrays/objects still matter when the old payload did
        # not declare the field.  Do not let a later empty value erase richer
        # data that was already staged.
        if key not in merged or value not in (None, "", [], {}):
            merged[key] = value
    if item_type == "chapter_summary":
        old_manifest = existing.get("coverage_manifest")
        new_manifest = incoming.get("coverage_manifest")
        if isinstance(old_manifest, dict) and isinstance(new_manifest, dict):
            merged["coverage_manifest"] = _merge_coverage_manifest(
                old_manifest,
                new_manifest,
            )
        old_scene_count = _positive_int(existing.get("scene_count"))
        new_scene_count = _positive_int(incoming.get("scene_count"))
        if old_scene_count or new_scene_count:
            merged["scene_count"] = max(old_scene_count, new_scene_count)
        # This is a write-control marker, never archival chapter metadata.
        merged.pop("coverage_manifest_mode", None)
    if item_type == "chapter_link":
        # This is a write-control marker, never chapter metadata.
        merged.pop("chapter_link_mode", None)
    return merged


def try_create_candidate(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    line: str,
    sort_order: int,
) -> dict[str, Any]:
    results = try_create_candidates(db, job, run, line, sort_order)
    if not results:
        return {}
    if len(results) == 1:
        return results[0]
    candidates = [result["candidate"] for result in results if result.get("candidate")]
    combined: dict[str, Any] = {"results": results, "candidates": candidates}
    if candidates:
        combined["candidate"] = candidates[0]
    skipped = [result.get("reason") for result in results if result.get("skipped")]
    if skipped:
        combined["skipped_reasons"] = skipped
    errors = [result for result in results if result.get("bad_line")]
    if errors:
        combined["errors"] = errors
    return combined


def try_create_candidates(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    line: str,
    sort_order: int,
) -> list[dict[str, Any]]:
    text = clean_jsonl_text(line)
    if not text:
        return []
    last_sort_order = (
        db.query(CatalogingCandidate.sort_order)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .order_by(CatalogingCandidate.sort_order.desc())
        .first()
    )
    next_sort_order = (
        int(last_sort_order[0] or 0) + 1
        if last_sort_order is not None
        else 0
    )
    base_sort_order = max(int(sort_order or 0), next_sort_order)
    try:
        parsed = parse_json_line(text)
        if parsed is None:
            return []
        return [
            create_candidate_from_raw(
                db,
                job,
                run,
                record,
                base_sort_order + offset,
            )
            for offset, record in enumerate(expand_candidate_records(parsed))
        ]
    except Exception as exc:
        return [{"bad_line": text, "error": str(exc)}]


def _preview_candidate_from_raw(
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    *,
    source_task: str,
) -> dict[str, Any] | None:
    """Normalize one record for coverage checks without writing it."""

    normalized = normalize_candidate(raw)
    _normalize_character_role_payload(normalized)
    _ensure_narrative_assessment_contract(
        normalized,
        source_task=source_task or normalized.get("source_task"),
    )
    _ensure_outline_identity(normalized, run)
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return None
    if _skip_reason_for_candidate(normalized):
        return None
    return {
        "item_type": normalized["item_type"],
        "status": "pending",
        "payload": normalized["payload"],
    }


def _existing_recovery_candidates(
    db: Session,
    run: CatalogingChapterRun,
) -> list[CatalogingCandidate]:
    return (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status != "rejected",
        )
        .all()
    )


def _preview_response_records(
    run: CatalogingChapterRun,
    records: list[dict[str, Any]],
    *,
    source_task: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid_records: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    for record in records:
        normalized = _preview_candidate_from_raw(
            run,
            record,
            source_task=source_task,
        )
        if normalized is None:
            continue
        valid_records.append(record)
        preview.append(normalized)
    return valid_records, preview


def recover_candidates_from_response_text(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    text: str,
    *,
    source_task: str = "response_recovery",
) -> dict[str, Any]:
    """Recover a complete candidate set from a provider's whole response.

    Streaming JSONL remains the fast path.  This boundary adapter is invoked
    before a retry and accepts common provider deviations such as a JSON array,
    pretty-printed JSON, fenced JSON, a collection wrapper, or a typed summary
    object containing the other candidate arrays.  Nothing is persisted unless
    the recovered records plus valid cards already in this run pass the same
    completeness gate as normal cataloging.
    """

    records = parse_candidate_response_records(text)
    valid_records, preview = _preview_response_records(
        run,
        records,
        source_task=source_task,
    )
    existing = _existing_recovery_candidates(db, run)
    preview_coverage = inspect_candidate_coverage(
        preview,
        db=db,
        project_id=job.project_id,
    )
    proposed = preview if preview_coverage.is_complete else [*existing, *preview]
    coverage = preview_coverage if preview_coverage.is_complete else inspect_candidate_coverage(
        proposed,
        db=db,
        project_id=job.project_id,
    )
    if not valid_records or not coverage.is_complete:
        return {
            "results": [],
            "coverage": coverage,
            "record_count": len(records),
        }

    sort_order = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
    ).count()
    results = [
        create_candidate_from_raw(
            db,
            job,
            run,
            record,
            sort_order + offset,
            source_task=source_task,
        )
        for offset, record in enumerate(valid_records)
    ]
    final_coverage = inspect_candidate_coverage(
        _existing_recovery_candidates(db, run),
        db=db,
        project_id=job.project_id,
    )
    return {
        "results": results,
        "coverage": final_coverage,
        "record_count": len(records),
    }


def recover_candidates_from_raw_output(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
) -> dict[str, Any]:
    """Recover the newest complete attempt stored on a failed chapter run."""

    attempts = candidate_response_attempts(run.raw_output or "")
    existing = _existing_recovery_candidates(db, run)
    combined_fallback: tuple[int, str] | None = None
    last_coverage = inspect_candidate_coverage(existing, db=db, project_id=job.project_id)

    # Prefer a self-contained attempt so records from different retries are not
    # mixed.  If no attempt is independently complete, allow the newest attempt
    # to complement cards already parsed from that same run.
    for reverse_index, attempt_text in enumerate(reversed(attempts), start=1):
        records = parse_candidate_response_records(attempt_text)
        _, preview = _preview_response_records(
            run,
            records,
            source_task="raw_output_recovery",
        )
        attempt_items = list(preview)
        combined_items = [*existing, *preview]
        attempt_coverage = inspect_candidate_coverage(
            attempt_items,
            db=db,
            project_id=job.project_id,
        )
        combined_coverage = inspect_candidate_coverage(
            combined_items,
            db=db,
            project_id=job.project_id,
        )
        last_coverage = combined_coverage
        if attempt_coverage.is_complete:
            recovered = recover_candidates_from_response_text(
                db,
                job,
                run,
                attempt_text,
                source_task="raw_output_recovery",
            )
            recovered["attempt_from_end"] = reverse_index
            return recovered
        if combined_fallback is None and combined_coverage.is_complete:
            combined_fallback = (reverse_index, attempt_text)

    if combined_fallback is not None:
        reverse_index, attempt_text = combined_fallback
        recovered = recover_candidates_from_response_text(
            db,
            job,
            run,
            attempt_text,
            source_task="raw_output_recovery",
        )
        recovered["attempt_from_end"] = reverse_index
        return recovered

    return {
        "results": [],
        "coverage": last_coverage,
        "record_count": 0,
        "attempt_from_end": None,
    }


def create_candidate_from_raw(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    sort_order: int,
    *,
    source_task: str | None = None,
) -> dict[str, Any]:
    try:
        normalized = normalize_candidate(raw)
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    _normalize_character_role_payload(normalized)
    _ensure_narrative_assessment_contract(
        normalized,
        source_task=source_task or normalized.get("source_task"),
    )
    ensure_outline_section_scene_number(db, run, normalized)
    _ensure_outline_identity(normalized, run)
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return {
            "bad_line": json.dumps(raw, ensure_ascii=False),
            "error": _unknown_type_message(raw, normalized),
        }
    skip_reason = _skip_reason_for_candidate(normalized)
    if skip_reason:
        return {"skipped": True, "reason": skip_reason}
    source_titles_error = _validate_worldbuilding_source_fact_titles(normalized)
    if source_titles_error:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": source_titles_error}
    target_error = _validate_worldbuilding_existing_target(
        db,
        job.project_id,
        normalized,
    )
    if target_error:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": target_error}
    try:
        validate_coverage_manifest_relationships(normalized["payload"])
        validate_character_state_target(
            db,
            job.project_id,
            normalized["item_type"],
            normalized["payload"],
            chapter_content=str(run.chapter.content or "") if run.chapter is not None else "",
        )
        validate_candidate_source_character_grounding(
            db,
            job.project_id,
            run,
            normalized,
        )
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    matching = _matching_candidate(db, job, run, normalized)
    if matching is None:
        identity_review_error = _validate_worldbuilding_create_identity_review(
            db,
            job.project_id,
            run,
            normalized,
        )
        if identity_review_error:
            return {
                "bad_line": json.dumps(raw, ensure_ascii=False),
                "error": identity_review_error,
            }
    if matching and _payload_from_candidate(matching) == normalized["payload"]:
        return {"duplicate": True}
    try:
        validate_character_profile_target(
            db, job.project_id, normalized["item_type"], normalized["payload"],
        )
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    if matching:
        old_payload = _payload_from_candidate(matching)
        preserve_worldbuilding_create_identity = (
            matching.item_type == "worldbuilding_create"
            and normalized["item_type"] == "worldbuilding_create"
            and bool(_worldbuilding_body_signature(old_payload))
            and _worldbuilding_body_signature(old_payload)
            == _worldbuilding_body_signature(normalized["payload"])
        )
        merged_item_type = (
            "worldbuilding_update"
            if {
                matching.item_type,
                normalized["item_type"],
            }
            <= {"worldbuilding_create", "worldbuilding_update"}
            and "worldbuilding_update"
            in {matching.item_type, normalized["item_type"]}
            else normalized["item_type"]
        )
        merged_payload = _merge_candidate_payload(
            old_payload,
            normalized["payload"],
            item_type=merged_item_type,
        )
        if preserve_worldbuilding_create_identity:
            for key in ("title", "entry_title"):
                if key in old_payload:
                    merged_payload[key] = old_payload[key]
        if merged_payload == old_payload:
            return {"duplicate": True}
        matching.raw_payload = json.dumps(merged_payload, ensure_ascii=False)
        matching.edited_payload = None
        matching.item_type = merged_item_type
        matching.operation = normalized["operation"] or matching.operation
        matching.target_type = normalized.get("target_type") or matching.target_type
        matching.target_id = normalized.get("target_id") or matching.target_id
        if not preserve_worldbuilding_create_identity:
            matching.target_name = (
                str(normalized.get("target_name") or "")[:200]
                or matching.target_name
            )
        matching.confidence = float_or_none(normalized.get("confidence")) or matching.confidence
        matching.evidence = (
            str(normalized.get("evidence") or "")[:2000]
            or matching.evidence
        )
        matching.source_task = source_task or normalized.get("source_task") or matching.source_task
        db.flush()
        return {"candidate": matching, "updated": True}
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
