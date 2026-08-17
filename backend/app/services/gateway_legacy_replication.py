"""Safe projection between canonical story rows and sync protocol entities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.services.chapter_ordering import next_chapter_sort_order
from app.services.character_service import character_to_dict, dumps_list, sync_character_aliases
from app.modules.continuity.infrastructure.models import (
    CausalEdge,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSummary,
    CharacterChangeLog,
    CharacterNarrativeState,
    CharacterTimeline,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
    NarrativeGovernanceEvent,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterSnapshot,
    Character,
    CharacterAlias,
    CharacterRelationship,
    CharacterVersion,
    OutlineNode,
    Project,
    WorldbuildingEntry,
    WorldbuildingRelation,
)

LOCAL_ONLY_COLUMNS = frozenset(
    {
        "folder_path",
        "storage_mode",
        "content_migrated_at",
        "content_file_path",
        "content_hash",
        "context_manifest_id",
        "operation_id",
    }
)


@dataclass(frozen=True)
class RecordSpec:
    model: type
    entity_type: str
    record_type: str
    project_mode: str
    defaults: dict[str, Any] | None = None


RECORD_SPECS = (
    RecordSpec(Project, "project", "project", "self", {"title": "未命名作品"}),
    RecordSpec(
        Chapter,
        "chapter",
        "chapter",
        "direct",
        {"title": "未命名章节", "content": ""},
    ),
    RecordSpec(ChapterSnapshot, "chapter_version", "chapter_snapshot", "chapter"),
    RecordSpec(
        OutlineNode,
        "outline",
        "outline_node",
        "direct",
        {"node_type": "chapter", "title": "未命名大纲"},
    ),
    RecordSpec(Character, "character", "character", "direct", {"name": "未命名角色"}),
    RecordSpec(CharacterVersion, "character", "character_version", "character"),
    RecordSpec(CharacterAlias, "character_alias", "character_alias", "direct"),
    RecordSpec(
        CharacterRelationship,
        "character_relation",
        "character_relationship",
        "direct",
    ),
    RecordSpec(
        WorldbuildingEntry,
        "world",
        "world_entry",
        "direct",
        {"dimension": "other", "title": "未命名设定", "content": ""},
    ),
    RecordSpec(WorldbuildingVersion, "world", "world_version", "world"),
    RecordSpec(
        WorldbuildingRelation,
        "world_relation",
        "world_relationship",
        "direct",
    ),
    RecordSpec(ChapterSummary, "summary", "chapter_summary", "chapter"),
    RecordSpec(CharacterTimeline, "timeline", "character_timeline", "character"),
    RecordSpec(CharacterChangeLog, "timeline", "character_change", "character"),
    RecordSpec(WorldbuildingTimeline, "timeline", "world_timeline", "world"),
    RecordSpec(Foreshadowing, "foreshadowing", "foreshadowing", "direct"),
    RecordSpec(CausalEdge, "governance", "causal_edge", "direct"),
    RecordSpec(NarrativeDebt, "governance", "narrative_debt", "direct"),
    RecordSpec(
        CharacterNarrativeState,
        "governance",
        "character_narrative_state",
        "direct",
    ),
    RecordSpec(
        NarrativeCheckpoint,
        "governance",
        "narrative_checkpoint",
        "direct",
    ),
    RecordSpec(
        ChapterQualityMetric,
        "governance",
        "chapter_quality_metric",
        "direct",
    ),
    RecordSpec(
        ChapterGovernanceReview,
        "governance",
        "chapter_governance_review",
        "direct",
    ),
    RecordSpec(
        NarrativeGovernanceEvent,
        "governance",
        "narrative_governance_event",
        "direct",
    ),
)

SPEC_BY_MODEL = {spec.model: spec for spec in RECORD_SPECS}
SPEC_BY_RECORD_TYPE = {spec.record_type: spec for spec in RECORD_SPECS}
DEFAULT_RECORD_TYPES = {
    "project": "project",
    "chapter": "chapter",
    "chapter_version": "chapter_snapshot",
    "outline": "outline_node",
    "character": "character",
    "character_alias": "character_alias",
    "character_relation": "character_relationship",
    "world": "world_entry",
    "world_relation": "world_relationship",
    "summary": "chapter_summary",
    "timeline": "character_timeline",
    "foreshadowing": "foreshadowing",
    "governance": "narrative_checkpoint",
}
CHARACTER_MUTATION_COLUMNS = frozenset(
    {
        "id",
        "project_id",
        "name",
        "appearance",
        "role_type",
        "personality",
        "background",
        "abilities",
        "age",
        "is_evolution_tracked",
        "life_status",
        "current_location",
        "realm_or_level",
        "physical_state",
        "mental_state",
        "current_goal",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
        "profile_json",
    }
)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds") + "Z"
    if isinstance(value, date):
        return value.isoformat()
    return value


def serialize_record(row: Any, spec: RecordSpec | None = None) -> dict[str, Any]:
    spec = spec or SPEC_BY_MODEL.get(type(row))
    if spec is None:
        raise ValidationError(f"不支持同步记录：{type(row).__name__}")
    if spec.model is Character:
        # Android and the web UI consume the same Character contract. Do not
        # leak DB-only shapes such as abilities JSON text or profile_json into
        # sync snapshots, otherwise bootstrap can replace a canonical PC API
        # response with an incompatible payload.
        return {"_record_type": spec.record_type, **character_to_dict(row)}
    payload: dict[str, Any] = {"_record_type": spec.record_type}
    for column in sa_inspect(spec.model).columns:
        if column.key in LOCAL_ONLY_COLUMNS:
            continue
        payload[column.key] = _json_value(getattr(row, column.key))
    return payload


def _project_for_parent(
    db: Session,
    parent_model: type,
    parent_id: str | None,
) -> str | None:
    if not parent_id:
        return None
    parent = db.get(parent_model, parent_id)
    return str(parent.project_id) if parent is not None else None


def project_id_for_record(db: Session, row: Any, spec: RecordSpec | None = None) -> str | None:
    spec = spec or SPEC_BY_MODEL.get(type(row))
    if spec is None:
        return None
    if spec.project_mode == "self":
        return str(row.id) if row.id else None
    if spec.project_mode == "direct":
        return str(row.project_id) if getattr(row, "project_id", None) else None
    if spec.project_mode == "chapter":
        return _project_for_parent(db, Chapter, getattr(row, "chapter_id", None))
    if spec.project_mode == "character":
        return _project_for_parent(db, Character, getattr(row, "character_id", None))
    if spec.project_mode == "world":
        return _project_for_parent(db, WorldbuildingEntry, getattr(row, "entry_id", None))
    return None


def _rows_for_spec(db: Session, project_id: str, spec: RecordSpec) -> list[Any]:
    query = db.query(spec.model)
    if spec.project_mode == "self":
        return query.filter(spec.model.id == project_id).all()
    if spec.project_mode == "direct":
        return query.filter(spec.model.project_id == project_id).all()
    if spec.project_mode == "chapter":
        return query.join(Chapter, spec.model.chapter_id == Chapter.id).filter(
            Chapter.project_id == project_id
        ).all()
    if spec.project_mode == "character":
        return query.join(Character, spec.model.character_id == Character.id).filter(
            Character.project_id == project_id
        ).all()
    if spec.project_mode == "world":
        return query.join(
            WorldbuildingEntry,
            spec.model.entry_id == WorldbuildingEntry.id,
        ).filter(WorldbuildingEntry.project_id == project_id).all()
    return []


def project_snapshots(db: Session, project_id: str) -> list[tuple[RecordSpec, Any, dict[str, Any]]]:
    snapshots: list[tuple[RecordSpec, Any, dict[str, Any]]] = []
    for spec in RECORD_SPECS:
        for row in _rows_for_spec(db, project_id, spec):
            snapshots.append((spec, row, serialize_record(row, spec)))
    snapshots.sort(key=lambda item: (item[0].entity_type, str(item[1].id)))
    return snapshots


def _coerce_column_value(column: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.removesuffix("Z"))
    if isinstance(column.type, Date) and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _spec_for_payload(entity_type: str, payload: dict[str, Any] | None) -> RecordSpec:
    record_type = str((payload or {}).get("_record_type") or DEFAULT_RECORD_TYPES.get(entity_type))
    spec = SPEC_BY_RECORD_TYPE.get(record_type)
    if spec is None or spec.entity_type != entity_type:
        raise ValidationError("同步实体类型与记录类型不匹配")
    return spec


def _spec_for_delete(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
) -> RecordSpec:
    for candidate in RECORD_SPECS:
        if candidate.entity_type != entity_type:
            continue
        row = db.get(candidate.model, entity_id)
        if row is not None and project_id_for_record(db, row, candidate) == project_id:
            return candidate
    return _spec_for_payload(entity_type, None)


def _assert_parent_project(
    db: Session,
    spec: RecordSpec,
    values: dict[str, Any],
    project_id: str,
) -> None:
    if spec.project_mode == "chapter":
        actual = _project_for_parent(db, Chapter, values.get("chapter_id"))
    elif spec.project_mode == "character":
        actual = _project_for_parent(db, Character, values.get("character_id"))
    elif spec.project_mode == "world":
        actual = _project_for_parent(db, WorldbuildingEntry, values.get("entry_id"))
    else:
        return
    if actual != project_id:
        raise ValidationError("同步记录引用的父实体不属于当前作品")


def _string_list(value: Any, *, field: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        normalized = raw.replace("，", ",").replace("、", ",").replace("\r", "\n")
        return [item.strip() for line in normalized.split("\n") for item in line.split(",") if item.strip()]
    raise ValidationError(f"角色 {field} 必须是字符串数组")


def _canonical_character_values(values: dict[str, Any]) -> tuple[dict[str, Any], list[str] | None]:
    """Translate the public PC Character contract back to persistence fields."""
    aliases = _string_list(values.pop("aliases", None), field="aliases")
    abilities = _string_list(values.get("abilities"), field="abilities")
    if abilities is not None:
        values["abilities"] = dumps_list(abilities)
    if "profile" in values:
        profile = values.pop("profile")
        if isinstance(profile, str):
            raw = profile.strip()
            if not raw:
                profile = {}
            else:
                try:
                    profile = json.loads(raw)
                except (TypeError, ValueError) as exc:
                    raise ValidationError("角色 profile 必须是 JSON 对象") from exc
        if profile is not None and not isinstance(profile, dict):
            raise ValidationError("角色 profile 必须是对象")
        values["profile_json"] = profile
    tracked = values.get("is_evolution_tracked")
    if isinstance(tracked, str):
        values["is_evolution_tracked"] = tracked.strip().lower() not in {
            "0", "false", "no", "off", "否"
        }
    return values, aliases


def apply_domain_mutation(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    operation: str,
    payload: dict[str, Any] | None,
) -> None:
    """Apply one validated protocol mutation to canonical authoring tables."""

    spec = (
        _spec_for_delete(
            db,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if operation == "delete"
        else _spec_for_payload(entity_type, payload)
    )
    row = db.get(spec.model, entity_id)
    if operation == "delete":
        if spec.model is Project:
            raise ValidationError("请在作品管理页确认删除，移动端不会直接删除整部作品")
        if row is None:
            return
        actual_project = project_id_for_record(db, row, spec)
        if actual_project != project_id:
            raise ValidationError("不能删除其他作品中的记录")
        db.delete(row)
        db.flush()
        return

    values = dict(payload or {})
    values.pop("_record_type", None)
    character_aliases: list[str] | None = None
    if spec.model is Character:
        values, character_aliases = _canonical_character_values(values)
    payload_id = values.get("id")
    if payload_id is not None and str(payload_id) != entity_id:
        raise ValidationError("同步记录 ID 与实体 ID 不一致")
    values["id"] = entity_id
    if spec.project_mode == "self":
        if entity_id != project_id:
            raise ValidationError("作品实体 ID 必须等于 project_id")
    elif spec.project_mode == "direct":
        supplied_project = values.get("project_id")
        if supplied_project is not None and str(supplied_project) != project_id:
            raise ValidationError("不能把记录移动到其他作品")
        values["project_id"] = project_id

    columns = {column.key: column for column in sa_inspect(spec.model).columns}
    allowed = {
        key: _coerce_column_value(columns[key], value)
        for key, value in values.items()
        if key in columns
        and key not in LOCAL_ONLY_COLUMNS
        and (spec.model is not Character or key in CHARACTER_MUTATION_COLUMNS)
    }
    for key, value in (spec.defaults or {}).items():
        allowed.setdefault(key, value)
    if spec.model is Chapter and row is None and "sort_order" not in allowed:
        allowed["sort_order"] = next_chapter_sort_order(db, project_id)
    _assert_parent_project(db, spec, allowed, project_id)

    if row is None:
        row = spec.model(**allowed)
        db.add(row)
    else:
        actual_project = project_id_for_record(db, row, spec)
        if actual_project != project_id:
            raise ValidationError("不能修改其他作品中的记录")
        for key, value in allowed.items():
            if key != "id":
                setattr(row, key, value)
    db.flush()
    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()


def spec_for_instance(row: Any) -> RecordSpec | None:
    return SPEC_BY_MODEL.get(type(row))


__all__ = [
    "LOCAL_ONLY_COLUMNS",
    "RECORD_SPECS",
    "RecordSpec",
    "apply_domain_mutation",
    "project_id_for_record",
    "project_snapshots",
    "serialize_record",
    "spec_for_instance",
]
