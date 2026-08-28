"""Entity projection and entity-level edits for structured creation data."""
from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.database.models_support import generate_uuid
from app.modules.creation.infrastructure.models import NovelCreationEntity, NovelCreationSession

ENTITY_COLLECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "world_style": (("worldbuilding", "worldbuilding"),),
    "characters": (("characters", "character"), ("relationships", "relationship")),
    "locations": (("entries", "place"), ("relations", "world_relation")),
    "macro_outline": (("volumes", "volume"),),
    "opening_outline": (("chapters", "chapter_outline"), ("sections", "scene_outline")),
}

ENTITY_TYPES_BY_ARTIFACT: dict[str, frozenset[str]] = {
    "world_style": frozenset({"worldbuilding"}),
    "characters": frozenset({"character", "relationship"}),
    "locations": frozenset({"location", "faction", "world_relation"}),
    "macro_outline": frozenset({"volume"}),
    "opening_outline": frozenset({"chapter_outline", "scene_outline"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _entity_type(default_type: str, row: dict[str, Any]) -> str:
    if default_type != "place":
        return default_type
    dimension = _text(row.get("dimension")).lower()
    if dimension in {"factions", "faction", "organization", "organisation", "势力", "组织"}:
        return "faction"
    return "location"


def _base_key(row: dict[str, Any], position: int) -> str:
    for field in ("id", "client_id", "name", "title"):
        value = _text(row.get(field))
        if value:
            return value[:160]
    source = _text(row.get("source_title") or row.get("source"))
    target = _text(row.get("target_title") or row.get("target"))
    if source or target:
        return f"{source}->{target}"[:160]
    return f"item-{position + 1}"


def _extract_records(artifact: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], int] = {}
    position = 0
    for field, default_type in ENTITY_COLLECTIONS.get(artifact, ()):
        rows = data.get(field)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            entity_type = _entity_type(default_type, row)
            base = _base_key(row, index)
            identity = (entity_type, base)
            occurrence = seen.get(identity, 0)
            seen[identity] = occurrence + 1
            entity_key = base if occurrence == 0 else f"{base}#{occurrence + 1}"
            provenance = row.get("_provenance") or row.get("provenance")
            records.append({
                "artifact": artifact,
                "field": field,
                "index": index,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "position": position,
                "data": deepcopy(row),
                "provenance": deepcopy(provenance) if isinstance(provenance, dict) else None,
            })
            position += 1
    return records


def serialize_creation_entity(entity: NovelCreationEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "session_id": entity.session_id,
        "artifact": entity.artifact_key,
        "entity_type": entity.entity_type,
        "entity_key": entity.entity_key,
        "position": int(entity.position or 0),
        "status": entity.status,
        "revision": int(entity.revision or 0),
        "source": entity.source,
        "data": deepcopy(entity.data_json),
        "provenance": deepcopy(entity.provenance_json),
        "created_at": entity.created_at.isoformat() if entity.created_at else None,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
        "deleted_at": entity.deleted_at.isoformat() if entity.deleted_at else None,
    }


def _entity_label(entity: NovelCreationEntity) -> str:
    data = entity.data_json if isinstance(entity.data_json, dict) else {}
    for field in ("name", "title"):
        value = _text(data.get(field))
        if value:
            return value[:160]
    source = _text(
        data.get("source_title")
        or data.get("character_a")
        or data.get("source")
    )
    target = _text(
        data.get("target_title")
        or data.get("character_b")
        or data.get("target")
    )
    if source or target:
        return f"{source} → {target}"[:160]
    return _text(entity.entity_key)[:160]


def _entity_excerpt(entity: NovelCreationEntity) -> str:
    data = entity.data_json if isinstance(entity.data_json, dict) else {}
    values: list[str] = []
    for field in (
        "summary", "description", "content", "goal", "current_goal",
        "identity", "role_type", "relation_type", "dimension",
    ):
        value = _text(data.get(field))
        if value and value not in values:
            values.append(value)
    return "；".join(values)[:320]


def serialize_creation_entity_summary(
    entity: NovelCreationEntity,
    *,
    match_score: int | None = None,
) -> dict[str, Any]:
    """Return a bounded index row; exact entity data stays behind the get tool."""

    payload = {
        "id": entity.id,
        "artifact": entity.artifact_key,
        "entity_type": entity.entity_type,
        "entity_key": entity.entity_key,
        "label": _entity_label(entity),
        "excerpt": _entity_excerpt(entity),
        "position": int(entity.position or 0),
        "status": entity.status,
        "revision": int(entity.revision or 0),
        "source": entity.source,
        "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
    }
    if match_score is not None:
        payload["match_score"] = match_score
    return payload


def _entity_search_score(entity: NovelCreationEntity, query: str) -> int:
    """Rank an author/model supplied query without inferring task intent."""

    needle = _text(query).casefold()
    if not needle:
        return 0
    data = entity.data_json if isinstance(entity.data_json, dict) else {}
    label = _entity_label(entity).casefold()
    key = _text(entity.entity_key).casefold()
    haystack = " ".join((
        label,
        key,
        _text(entity.entity_type).casefold(),
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).casefold(),
    ))
    if needle in (label, key):
        return 100
    if needle in label or needle in key:
        return 90
    if needle in haystack:
        return 70
    terms = [term for term in needle.split() if term]
    matches = sum(1 for term in terms if term in haystack)
    if not matches:
        return -1
    return min(60, 20 + matches * 10)


def query_creation_entities(
    session: NovelCreationSession,
    *,
    artifact: str | None = None,
    entity_type: str | None = None,
    include_deleted: bool = False,
    query: str = "",
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Fence, rank and paginate entity summaries for model-controlled retrieval."""

    ensure_creation_entities(session)
    rows = [
        item
        for item in session.entities
        if (not artifact or item.artifact_key == artifact)
        and (not entity_type or item.entity_type == entity_type)
        and (include_deleted or item.status != "deleted")
    ]
    needle = _text(query)
    ranked: list[tuple[int, NovelCreationEntity]] = []
    for item in rows:
        score = _entity_search_score(item, needle)
        if needle and score < 0:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda pair: (
        -pair[0] if needle else 0,
        pair[1].artifact_key,
        int(pair[1].position or 0),
        pair[1].entity_key,
    ))
    total = len(ranked)
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(int(limit or 20), 50))
    selected = ranked[safe_offset:safe_offset + safe_limit]
    return {
        "entities": [
            serialize_creation_entity_summary(item, match_score=score if needle else None)
            for score, item in selected
        ],
        "query": needle,
        "offset": safe_offset,
        "limit": safe_limit,
        "total": total,
        "has_more": safe_offset + len(selected) < total,
        "next_offset": safe_offset + len(selected) if safe_offset + len(selected) < total else None,
    }


def sync_creation_entities(
    session: NovelCreationSession,
    artifact: str,
    data: dict[str, Any],
    *,
    revision: int,
    source: str,
) -> list[NovelCreationEntity]:
    if artifact not in ENTITY_COLLECTIONS:
        return []
    records = _extract_records(artifact, data)
    existing = {
        (item.entity_type, item.entity_key): item
        for item in session.entities
        if item.artifact_key == artifact
    }
    active: set[tuple[str, str]] = set()
    now = datetime.utcnow()
    projected: list[NovelCreationEntity] = []
    for record in records:
        identity = (record["entity_type"], record["entity_key"])
        active.add(identity)
        entity = existing.get(identity)
        if entity is None:
            entity = NovelCreationEntity(
                id=generate_uuid(),
                artifact_key=artifact,
                entity_type=record["entity_type"],
                entity_key=record["entity_key"],
                created_at=now,
            )
            session.entities.append(entity)
        entity.position = record["position"]
        entity.status = "active"
        entity.revision = int(revision)
        entity.source = source or "unknown"
        entity.data_json = record["data"]
        entity.provenance_json = record["provenance"]
        entity.updated_at = now
        entity.deleted_at = None
        projected.append(entity)
    for identity, entity in existing.items():
        if identity not in active and entity.status != "deleted":
            entity.status = "deleted"
            entity.revision = int(revision)
            entity.updated_at = now
            entity.deleted_at = now
    return projected


def ensure_creation_entities(session: NovelCreationSession) -> int:
    draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    count = 0
    for artifact in ENTITY_COLLECTIONS:
        state = stages.get(artifact) if isinstance(stages.get(artifact), dict) else {}
        data = state.get("data")
        if isinstance(data, dict):
            count += len(sync_creation_entities(
                session,
                artifact,
                data,
                revision=int(session.revision or 0),
                source=_text(state.get("source")) or "legacy_projection",
            ))
    return count


def list_creation_entities(
    session: NovelCreationSession,
    *,
    artifact: str | None = None,
    entity_type: str | None = None,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    ensure_creation_entities(session)
    rows = [
        item
        for item in session.entities
        if (not artifact or item.artifact_key == artifact)
        and (not entity_type or item.entity_type == entity_type)
        and (include_deleted or item.status != "deleted")
    ]
    rows.sort(key=lambda item: (item.artifact_key, int(item.position or 0), item.entity_key))
    return [serialize_creation_entity(item) for item in rows]


def get_creation_entity(db: Session, entity_id: str) -> NovelCreationEntity | None:
    return db.get(NovelCreationEntity, entity_id)


def _entity_pointer(session: NovelCreationSession, entity: NovelCreationEntity) -> str:
    draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    state = stages.get(entity.artifact_key) if isinstance(stages.get(entity.artifact_key), dict) else {}
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    for record in _extract_records(entity.artifact_key, data):
        if record["entity_type"] == entity.entity_type and record["entity_key"] == entity.entity_key:
            return f"/{record['field']}/{record['index']}"
    raise ValueError("实体已不在当前立项数据中，请刷新后重试")


def _dependent_remove_paths(session: NovelCreationSession, entity: NovelCreationEntity) -> list[str]:
    draft = session.draft_json if isinstance(session.draft_json, dict) else {}
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    state = stages.get(entity.artifact_key) if isinstance(stages.get(entity.artifact_key), dict) else {}
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    value = _text(entity.data_json.get("name") or entity.data_json.get("title"))
    if not value:
        return []
    if entity.artifact_key == "characters":
        rows = data.get("relationships") if isinstance(data.get("relationships"), list) else []
        fields = ("character_a", "character_b", "source", "target")
        collection = "relationships"
    elif entity.artifact_key == "locations":
        rows = data.get("relations") if isinstance(data.get("relations"), list) else []
        fields = ("source_title", "target_title", "source", "target")
        collection = "relations"
    else:
        return []
    indexes = [
        index
        for index, row in enumerate(rows)
        if isinstance(row, dict) and any(_text(row.get(field)) == value for field in fields)
    ]
    return [f"/{collection}/{index}" for index in reversed(indexes)]


def patch_creation_entity(
    session: NovelCreationSession,
    entity: NovelCreationEntity,
    changes: list[dict[str, Any]],
    *,
    expected_revision: int,
    source: str = "author",
    patcher: Callable[..., dict[str, Any]],
    validator: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    if entity.session_id != session.id:
        raise ValueError("实体不属于当前立项会话")
    if entity.status == "deleted":
        raise ValueError("实体已删除")
    if int(session.revision or 0) != int(expected_revision):
        raise RuntimeError("revision_conflict")
    prefix = _entity_pointer(session, entity)
    scoped = []
    for change in changes:
        path = _text(change.get("path"))
        if path in {"", "/"}:
            full_path = prefix
        elif path.startswith("/"):
            full_path = prefix + path
        else:
            raise ValueError("实体 Patch 路径必须使用 JSON Pointer")
        item = deepcopy(change)
        item["path"] = full_path
        scoped.append(item)
    result = patcher(
        session,
        entity.artifact_key,
        scoped,
        source=source,
        validator=validator,
    )
    refreshed = next((item for item in session.entities if item.id == entity.id), entity)
    return {
        "entity": serialize_creation_entity(refreshed),
        "artifact": result["artifact"],
        "changes": result["changes"],
        "affected_artifacts": result["affected_artifacts"],
    }


def delete_creation_entity(
    session: NovelCreationSession,
    entity: NovelCreationEntity,
    *,
    expected_revision: int,
    source: str = "author",
    patcher: Callable[..., dict[str, Any]],
    validator: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    if int(session.revision or 0) != int(expected_revision):
        raise RuntimeError("revision_conflict")
    pointer = _entity_pointer(session, entity)
    changes = [
        *({"path": path, "action": "remove"} for path in _dependent_remove_paths(session, entity)),
        {"path": pointer, "action": "remove"},
    ]
    result = patcher(
        session,
        entity.artifact_key,
        changes,
        source=source,
        validator=validator,
    )
    return {
        "entity": serialize_creation_entity(entity),
        "artifact": result["artifact"],
        "affected_artifacts": result["affected_artifacts"],
    }


__all__ = [
    "ENTITY_COLLECTIONS",
    "ENTITY_TYPES_BY_ARTIFACT",
    "delete_creation_entity",
    "ensure_creation_entities",
    "get_creation_entity",
    "list_creation_entities",
    "patch_creation_entity",
    "query_creation_entities",
    "serialize_creation_entity",
    "serialize_creation_entity_summary",
    "sync_creation_entities",
]
