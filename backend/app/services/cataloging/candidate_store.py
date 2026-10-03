"""Stage validated native records from the current cataloging plan."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    WorldbuildingEntry,
)
from ...database.query_filters import (
    is_current_worldbuilding_status,
)
from ...modules.continuity.domain.cataloging_contract import (
    validate_coverage_manifest_relationships,
)
from ..story_granularity import (
    CHARACTER_STABLE_FIELDS,
    CHARACTER_STATE_FIELDS,
    has_chapter_narrative_state,
)
from .candidate_io import float_or_none
from .candidate_merge import _merge_candidate_payload
from .candidate_validation import (
    validate_candidate_source_character_grounding,
)
from .character_targets import (
    ArchiveValueMismatch,
    validate_character_profile_target,
    validate_character_state_target,
)
from ...modules.continuity.domain.outline_character_contract import outline_character_ids
from .constants import VALID_ITEM_TYPES
from .records import normalize_candidate
from .scene_contract import (
    is_scene_replacement,
    scene_repair_context,
    validate_scene_candidate,
    validate_scene_replacement,
)


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


def _clean_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean_value(item) for item in value).strip()
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).strip()
    return str(value or "").strip()


def _has_any_text(payload: dict[str, Any], keys: set[str] | tuple[str, ...]) -> bool:
    return any(_clean_value(payload.get(key)) for key in keys)


def _is_placeholder_name(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


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


def _validate_worldbuilding_source_labels(
    normalized: dict[str, Any],
) -> str | None:
    payload = normalized.get("payload")
    if not isinstance(payload, dict) or "source_labels" not in payload:
        return None
    if normalized.get("item_type") not in {
        "worldbuilding_create",
        "worldbuilding_update",
        "worldbuilding_timeline",
    }:
        return (
            f"source_labels 只能用于世界观候选，当前候选是 {normalized.get('item_type')}；"
            "请从当前候选中移除该字段，保留其余有效字段。若要声明事实归属，另行输出对应的 "
            "worldbuilding_create、worldbuilding_update 或 worldbuilding_timeline 候选，"
            "在其 payload 中填写 source_labels 字符串数组，不能写进 chapter_summary、"
            "coverage_manifest 或 chapter_link，也不能写成带 worldbuilding 键的对象"
        )
    values = payload.get("source_labels")
    if (
        not isinstance(values, list)
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        return (
            "source_labels 必须是非空字符串数组，例如 [\"原事实称呼\"]，"
            "不能写成 {\"worldbuilding\":[...]} 对象"
        )
    payload["source_labels"] = list(
        dict.fromkeys(value.strip() for value in values)
    )
    return None


def _validate_worldbuilding_existing_target(
    db: Session,
    project_id: str,
    run: CatalogingChapterRun,
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
    if entry is None and item_type == "worldbuilding_timeline":
        creates = db.query(CatalogingCandidate).filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.item_type == "worldbuilding_create",
            CatalogingCandidate.status != "rejected",
        ).all()
        if any(_payload_from_candidate(row).get("client_id") == target_id for row in creates):
            return None
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


def create_candidate_from_raw(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    raw: dict[str, Any],
    sort_order: int,
    *,
    source_task: str | None = None,
) -> dict[str, Any]:
    if is_scene_replacement(raw):
        return replace_scene_candidates(db, job, run, raw, sort_order)
    normalized, error = _prepare_candidate(db, job, run, raw, source_task)
    if error is not None:
        return error
    try:
        matching = _matching_candidate(db, job, run, normalized)
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    if matching and _payload_from_candidate(matching) == normalized["payload"]:
        return {"duplicate": True}
    if matching and matching.status == "applied":
        return {"bad_line": json.dumps(raw, ensure_ascii=False),
                "error": "该候选已写入，不能在重试中修改已应用记录；请只修复未完成候选",
                "repair_context": {"applied_candidate_id": matching.id}}
    if matching and (matching.edited_payload is not None or matching.status in {"edited", "approved"}):
        return {"bad_line": json.dumps(raw, ensure_ascii=False),
                "error": "该候选已由作者编辑或确认，模型修复不能覆盖作者内容；请保留并修复其他缺项",
                "repair_context": {"author_candidate_id": matching.id}}
    try:
        validate_character_profile_target(
            db, job.project_id, normalized["item_type"], normalized["payload"],
        )
    except ArchiveValueMismatch as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc),
                "repair_context": exc.repair_context}
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    if matching:
        old_payload = _payload_from_candidate(matching)
        merged_item_type = normalized["item_type"]
        try:
            merged_payload = _merge_candidate_payload(
                old_payload,
                normalized["payload"],
                item_type=merged_item_type,
            )
            if merged_item_type in {"outline_create", "outline_update"}:
                outline_character_ids(merged_payload)
        except ValueError as exc:
            # The same store is used by streaming and whole-response recovery.
            # Invalid model fields must remain repairable on both paths; letting
            # this escape caused recovery to raise again and bypass all retries.
            return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
        if merged_payload == old_payload:
            return {"duplicate": True}
        try:
            # Validate the complete record that will actually be applied.
            # A valid delta can retain stale guarded fields from an earlier
            # candidate, so validating only the incoming fragment is insufficient.
            validate_character_profile_target(db, job.project_id, merged_item_type, merged_payload)
            validate_character_state_target(
                db, job.project_id, merged_item_type, merged_payload,
                chapter_content=str(run.chapter.content or "") if run.chapter is not None else "",
                chapter_run_id=run.id,
            )
            validate_coverage_manifest_relationships(merged_payload)
            from ...modules.continuity.domain.candidate_contract import validate_candidate_fields
            validate_candidate_fields(merged_item_type, merged_payload)
            validate_scene_candidate(db, run, {"item_type": merged_item_type, "payload": merged_payload})
            validate_candidate_source_character_grounding(db, job.project_id, run,
                {"item_type": merged_item_type, "payload": merged_payload})
        except ArchiveValueMismatch as exc:
            return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc),
                    "repair_context": exc.repair_context}
        except ValueError as exc:
            return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
        matching.raw_payload = json.dumps(merged_payload, ensure_ascii=False)
        matching.edited_payload = None
        matching.item_type = merged_item_type
        matching.operation = normalized["operation"] or matching.operation
        matching.target_type = normalized.get("target_type") or matching.target_type
        matching.target_id = normalized.get("target_id") or matching.target_id
        matching.target_name = str(normalized.get("target_name") or "")[:200] or matching.target_name
        matching.confidence = float_or_none(normalized.get("confidence")) or matching.confidence
        matching.evidence = (
            str(normalized.get("evidence") or "")[:2000]
            or matching.evidence
        )
        matching.source_task = source_task or normalized.get("source_task") or matching.source_task
        if matching.status == "apply_failed":
            matching.status = "pending"
        matching.error = None
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


def replace_scene_candidates(
    db: Session, job: CatalogingJob, run: CatalogingChapterRun,
    raw: dict[str, Any], sort_order: int,
) -> dict[str, Any]:
    try:
        targets, sections, marker, duplicate = validate_scene_replacement(db, run, raw)
        if duplicate:
            return {"duplicate": True}
        with db.begin_nested():
            for row in targets:
                row.status = "rejected"
            db.flush()
            replacements = []
            for index, section in enumerate(sections):
                result = create_candidate_from_raw(db, job, run, section, sort_order + index)
                if not result.get("candidate"):
                    raise ValueError(result.get("error") or "替换场景未通过候选校验")
                replacements.append(result["candidate"])
            receipt = marker + ",".join(row.id for row in replacements)
            for row in targets:
                row.error = receipt
            db.flush()
        return {"candidates": replacements, "scene_plan_replaced": True}
    except ValueError as exc:
        return {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc),
                "scene_repair": scene_repair_context(db, run)}


def _prepare_candidate(
    db: Session, job: CatalogingJob, run: CatalogingChapterRun,
    raw: dict[str, Any], source_task: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        normalized = normalize_candidate(raw)
    except ValueError as exc:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    try:
        validate_scene_candidate(db, run, normalized)
        if normalized["item_type"] in {"outline_create", "outline_update"}:
            outline_character_ids(normalized["payload"])
    except ValueError as exc:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc),
                "scene_repair": scene_repair_context(db, run)}
    if normalized["item_type"] not in VALID_ITEM_TYPES:
        return None, {
            "bad_line": json.dumps(raw, ensure_ascii=False),
            "error": _unknown_type_message(raw, normalized),
        }
    skip_reason = _skip_reason_for_candidate(normalized)
    if skip_reason:
        return None, {"skipped": True, "reason": skip_reason}
    source_titles_error = _validate_worldbuilding_source_labels(normalized)
    if source_titles_error:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": source_titles_error}
    target_error = _validate_worldbuilding_existing_target(
        db,
        job.project_id,
        run,
        normalized,
    )
    if target_error:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": target_error}
    try:
        validate_coverage_manifest_relationships(normalized["payload"])
        state_target = validate_character_state_target(
            db,
            job.project_id,
            normalized["item_type"],
            normalized["payload"],
            chapter_content=str(run.chapter.content or "") if run.chapter is not None else "",
            chapter_run_id=run.id,
        )
        if normalized["item_type"] == "character_state_update" and state_target is None:
            payload = normalized["payload"]
            staged_ids = {
                _payload_from_candidate(row).get("client_id")
                for row in db.query(CatalogingCandidate).filter(
                    CatalogingCandidate.chapter_run_id == run.id,
                    CatalogingCandidate.item_type == "character_create",
                    CatalogingCandidate.status != "rejected",
                ).all()
            }
            if payload.get("id") not in staged_ids:
                raise ValueError("角色状态目标不存在；新角色须先提交 character_create，再引用相同 client_id")
        validate_candidate_source_character_grounding(
            db,
            job.project_id,
            run,
            normalized,
        )
    except ArchiveValueMismatch as exc:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc),
                "repair_context": exc.repair_context}
    except ValueError as exc:
        return None, {"bad_line": json.dumps(raw, ensure_ascii=False), "error": str(exc)}
    return normalized, None


def _record_key(kind: str, payload: dict[str, Any]) -> tuple:
    if kind in {"chapter_summary", "chapter_link"}:
        return (kind,)
    if kind in {"outline_create", "outline_update"}:
        node_type = payload["node_type"]
        identity = "chapter"
        if node_type == "section":
            identity = payload.get("scene_number")
        elif node_type == "volume":
            identity = payload.get("id") or payload.get("title")
        return ("outline", node_type, identity)
    if kind == "character_relationship":
        return (kind, payload["source_name"], payload["target_name"])
    if kind == "character_merge_candidate":
        return (kind, payload["primary_name"], payload["secondary_name"])
    identity = payload.get("id") or payload.get("client_id")
    if kind.endswith("_timeline"):
        return (kind, identity, payload.get("event_type"),
                payload.get("sort_order", payload.get("event_description")))
    return (kind, identity)


def _matching_candidate(db: Session, job: CatalogingJob, run: CatalogingChapterRun,
                        normalized: dict[str, Any]) -> CatalogingCandidate | None:
    """Checkpoint identity comes only from structured IDs and scene positions."""
    wanted = _record_key(normalized["item_type"], normalized["payload"])
    rows = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.status != "rejected",
    ).order_by(CatalogingCandidate.sort_order).all()
    for row in rows:
        try:
            key = _record_key(row.item_type, _payload_from_candidate(row))
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"既有候选 {row.id} 缺少当前计划的结构字段；请读取该候选，"
                "未经作者编辑或确认时可用 reject_candidate_ids 明确撤回后重新提交"
            ) from exc
        if key == wanted:
            return row
    return None
