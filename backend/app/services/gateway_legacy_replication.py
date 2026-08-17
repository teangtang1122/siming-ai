"""Safe projection between canonical story rows and sync protocol entities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.core.db_helpers import get_outline_node_or_404
from app.core.exceptions import ValidationError
from app.core.utils import count_words
from app.modules.continuity.infrastructure.governance import (
    STATUS_UPDATE_FIELDS,
    apply_governance_status_update,
)
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
from app.modules.story.infrastructure.chapter_evidence import SqlAlchemyChapterEvidenceReader
from app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
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
from app.services.chapter_ordering import next_chapter_sort_order
from app.services.chapter_service import chapter_to_detail, create_snapshot
from app.services.character_role_types import (
    append_character_role_description,
    normalize_character_role_type,
)
from app.services.character_service import (
    character_to_dict,
    create_character_version,
    dumps_list,
    sync_character_aliases,
)
from app.services.narrative_governance import create_narrative_checkpoint
from app.services.outline_service import (
    ensure_no_cycle,
    load_outline_nodes,
    node_to_dict,
    outline_sort_context,
    replace_character_links,
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

MUTATION_COLUMNS_BY_MODEL: dict[type, frozenset[str]] = {
    Project: frozenset(
        {
            "id",
            "title",
            "description",
            "tags",
            "narrative_perspective",
            "writing_style",
            "forbidden_sentence_patterns",
            "rhetoric_guidelines",
            "short_sentences",
            "custom_style_prompt",
            "daily_word_goal",
        }
    ),
    Chapter: frozenset(
        {"id", "project_id", "title", "outline_node_id", "content", "context_manifest_id"}
    ),
    OutlineNode: frozenset(
        {
            "id",
            "project_id",
            "parent_id",
            "node_type",
            "title",
            "summary",
            "status",
            "sort_order",
            "metadata_json",
        }
    ),
    Character: CHARACTER_MUTATION_COLUMNS,
    WorldbuildingEntry: frozenset(
        {"id", "project_id", "dimension", "title", "content", "sort_order"}
    ),
    Foreshadowing: frozenset(
        {
            "id",
            "project_id",
            "title",
            "description",
            "status",
            "importance",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "evidence",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "closed_by",
            "storyline",
            "dedupe_key",
            "source",
        }
    ),
    NarrativeDebt: frozenset(
        {
            "id",
            "project_id",
            "debt_type",
            "title",
            "description",
            "status",
            "priority",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "linked_foreshadowing_id",
            "linked_causal_edge_id",
            "evidence",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "closed_by",
            "dedupe_key",
            "source",
        }
    ),
}


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
    if spec.model is OutlineNode:
        return {"_record_type": spec.record_type, **node_to_dict(row)}
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


def _serialize_domain_row(db: Session, row: Any, spec: RecordSpec) -> dict[str, Any]:
    if spec.model is Chapter:
        context = outline_sort_context(load_outline_nodes(db, str(row.project_id)))
        return {"_record_type": spec.record_type, **chapter_to_detail(row, context)}
    return serialize_record(row, spec)


def domain_snapshot_for_entity(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    """Return the authoritative PC-shaped snapshot after a sync mutation."""
    for spec in RECORD_SPECS:
        if spec.entity_type != entity_type:
            continue
        row = db.get(spec.model, entity_id)
        if row is None:
            continue
        if project_id_for_record(db, row, spec) != project_id:
            continue
        return _serialize_domain_row(db, row, spec)
    return None


def project_snapshots(db: Session, project_id: str) -> list[tuple[RecordSpec, Any, dict[str, Any]]]:
    snapshots: list[tuple[RecordSpec, Any, dict[str, Any]]] = []
    for spec in RECORD_SPECS:
        for row in _rows_for_spec(db, project_id, spec):
            snapshots.append((spec, row, _serialize_domain_row(db, row, spec)))
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
        return [
            item.strip()
            for line in normalized.split("\n")
            for item in line.split(",")
            if item.strip()
        ]
    raise ValidationError(f"角色 {field} 必须是字符串数组")


def _canonical_project_values(values: dict[str, Any]) -> dict[str, Any]:
    tags = values.get("tags")
    if isinstance(tags, list):
        values["tags"] = json.dumps([str(item) for item in tags], ensure_ascii=False)
    elif tags is not None and not isinstance(tags, str):
        raise ValidationError("作品 tags 必须是字符串数组")
    return values


def _canonical_outline_values(
    values: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[str, str | None]] | None]:
    if "metadata" in values:
        metadata = values.pop("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValidationError("大纲 metadata 必须是对象")
        values["metadata_json"] = metadata

    characters = values.pop("characters", None)
    character_ids = values.pop("character_ids", None)
    links: list[tuple[str, str | None]] | None = None
    if characters is not None:
        if not isinstance(characters, list):
            raise ValidationError("大纲 characters 必须是数组")
        links = []
        seen: set[str] = set()
        for item in characters:
            if not isinstance(item, dict):
                raise ValidationError("大纲 characters 元素必须是对象")
            character_id = str(item.get("character_id") or "").strip()
            if not character_id or character_id in seen:
                continue
            seen.add(character_id)
            role = str(item.get("role_in_scene") or "").strip() or None
            links.append((character_id, role))
    elif character_ids is not None:
        if not isinstance(character_ids, list):
            raise ValidationError("大纲 character_ids 必须是数组")
        links = []
        seen: set[str] = set()
        for raw in character_ids:
            character_id = str(raw or "").strip()
            if not character_id or character_id in seen:
                continue
            seen.add(character_id)
            links.append((character_id, None))
    return values, links


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


def _prepare_character_mutation_values(
    values: dict[str, Any],
    row: Character | None,
) -> tuple[dict[str, Any], list[str] | None, str | None]:
    raw_summary = values.pop("change_summary", None)
    change_summary = str(raw_summary or "").strip() or None
    if row is None and "role_type" not in values:
        values["role_type"] = normalize_character_role_type(None)
    if "role_type" in values:
        raw_role_type = values["role_type"]
        values["background"] = append_character_role_description(
            values.get("background", row.background if row is not None else None),
            raw_role_type,
        )
        values["role_type"] = normalize_character_role_type(
            raw_role_type,
            default=(row.role_type or "other") if row is not None else "other",
        )
    values, aliases = _canonical_character_values(values)
    return values, aliases, change_summary


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
    if spec.model not in MUTATION_COLUMNS_BY_MODEL:
        raise ValidationError("该同步记录由 PC 管理，移动端只读")
    row = db.get(spec.model, entity_id)
    row_existed = row is not None
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
    character_change_summary: str | None = None
    outline_links: list[tuple[str, str | None]] | None = None
    governance_status_values: dict[str, Any] | None = None
    if spec.model in {Foreshadowing, NarrativeDebt}:
        governance_status_values = {
            key: values.pop(key)
            for key in STATUS_UPDATE_FIELDS
            if key in values
        }
    if spec.model is Project:
        values = _canonical_project_values(values)
    if spec.model is Character:
        values, character_aliases, character_change_summary = _prepare_character_mutation_values(
            values,
            row,
        )
    if spec.model is OutlineNode:
        values, outline_links = _canonical_outline_values(values)
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

    if spec.model is Chapter and row_existed:
        chapter_values = {
            key: value
            for key, value in values.items()
            if key in {"title", "outline_node_id", "content", "context_manifest_id"}
        }
        SqlAlchemyChapterWorkspace(db).save(project_id, entity_id, chapter_values)
        return

    if spec.model is Chapter and values.get("outline_node_id"):
        get_outline_node_or_404(db, project_id, values.get("outline_node_id"))
    if spec.model is OutlineNode and "parent_id" in values:
        ensure_no_cycle(db, project_id, entity_id, values.get("parent_id"))

    columns = {column.key: column for column in sa_inspect(spec.model).columns}
    mutation_columns = MUTATION_COLUMNS_BY_MODEL[spec.model]
    allowed = {
        key: _coerce_column_value(columns[key], value)
        for key, value in values.items()
        if key in columns and key in mutation_columns
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
    if governance_status_values:
        item_type = "foreshadowing" if spec.model is Foreshadowing else "narrative_debt"
        updated = apply_governance_status_update(
            db,
            SqlAlchemyChapterEvidenceReader(),
            project_id,
            item_type,
            row.id,
            governance_status_values,
            commit=False,
        )
        if updated is None:
            raise ValidationError("治理项不存在")
    if spec.model is Chapter and "content" in allowed:
        row.word_count = count_words(row.content or "")
        db.flush()
    if spec.model is Chapter and not row_existed:
        db.add(create_snapshot(row, "manual_save"))
        create_narrative_checkpoint(
            db,
            project_id,
            chapter=row,
            label=f"{row.title} 创建",
            trigger_type="chapter_create",
        )
        db.flush()
    if spec.model is OutlineNode and outline_links is not None:
        replace_character_links(db, project_id, row, outline_links)
        db.flush()
    if spec.model is Character and character_aliases is not None:
        sync_character_aliases(db, row, character_aliases)
        db.flush()
    if spec.model is Character and row_existed:
        create_character_version(
            db,
            row,
            character_change_summary or "手动更新角色档案",
        )
        db.flush()


def spec_for_instance(row: Any) -> RecordSpec | None:
    return SPEC_BY_MODEL.get(type(row))


__all__ = [
    "LOCAL_ONLY_COLUMNS",
    "RECORD_SPECS",
    "RecordSpec",
    "apply_domain_mutation",
    "domain_snapshot_for_entity",
    "project_id_for_record",
    "project_snapshots",
    "serialize_record",
    "spec_for_instance",
]
