"""Bounded read and generation projections for conversational novel creation."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.database.models import NovelCreationSession
from app.services.novel_creation_authoring import _author_context
from app.services.novel_creation_entities import ENTITY_COLLECTIONS
from app.services.novel_creation_workspace import (
    STAGE_LABELS,
    STAGE_ORDER,
    initialize_session_draft,
    serialize_creation_artifact,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def artifact_data_shape(stage: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "has_data": False,
            "top_level_fields": [],
            "collection_counts": {},
            "approximate_chars": 0,
        }
    collections = {field for field, _entity_type in ENTITY_COLLECTIONS.get(stage, ())}
    return {
        "has_data": True,
        "top_level_fields": sorted(str(key) for key in data),
        "collection_counts": {
            field: len(data.get(field))
            for field in sorted(collections)
            if isinstance(data.get(field), list)
        },
        "approximate_chars": len(json.dumps(data, ensure_ascii=False, default=str)),
    }


def _artifact_overview(
    stage: str,
    state: dict[str, Any],
    locks: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    locked_paths = locks.get(stage) if isinstance(locks.get(stage), list) else []
    return {
        "artifact": stage,
        "label": STAGE_LABELS[stage],
        "status": _text(state.get("status")) or "pending",
        "source": _text(state.get("source")) or "unknown",
        "updated_at": state.get("updated_at"),
        "stale_reason": state.get("stale_reason"),
        "locked_path_count": len(locked_paths),
        "revision": revision,
        "data_shape": artifact_data_shape(stage, state.get("data")),
    }


def project_creation_artifact(
    session: NovelCreationSession,
    stage: str,
) -> dict[str, Any]:
    """Return exact scalar fields while keeping object collections behind entity reads."""

    artifact = serialize_creation_artifact(session, stage)
    data = artifact.get("data") if isinstance(artifact.get("data"), dict) else None
    collection_fields = [field for field, _entity_type in ENTITY_COLLECTIONS.get(stage, ())]
    if not data or not collection_fields:
        return artifact
    artifact["data"] = {
        key: deepcopy(value)
        for key, value in data.items()
        if key not in collection_fields
    }
    artifact["omitted_collections"] = {
        field: len(data.get(field)) if isinstance(data.get(field), list) else 0
        for field in collection_fields
    }
    artifact["collection_access"] = (
        "Use list_creation_entities with artifact/query/limit, then get_creation_entity "
        "for the exact objects needed by the latest user request."
    )
    return artifact


def compact_creation_snapshot(session: NovelCreationSession) -> dict[str, Any]:
    """Return a bounded index; detailed facts are retrieved by artifact/entity ID."""

    draft = deepcopy(initialize_session_draft(session, persist=False))
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    locks = draft.get("artifact_locks") if isinstance(draft.get("artifact_locks"), dict) else {}
    revision = int(session.revision or 0)
    form = draft.get("form") if isinstance(draft.get("form"), dict) else {}
    locked_requirements = (
        draft.get("locked_requirements")
        if isinstance(draft.get("locked_requirements"), list)
        else []
    )
    return {
        "revision": revision,
        "session": {
            "id": session.id,
            "source_project_id": session.source_project_id,
            "created_project_id": session.created_project_id,
            "status": session.status,
            "mode": session.mode,
            "schema_version": int(draft.get("schema_version") or session.schema_version or 1),
            "current_stage": session.current_stage,
            "revision": revision,
            "target_audience": session.target_audience,
            "genre": session.genre,
            "platform": session.platform,
            "creation_mode": _text(draft.get("creation_mode")) or "explore",
            "selected_concept_id": draft.get("selected_concept_id"),
            "author_input": {
                "brief_available": bool(_text(session.user_brief) or _text(form.get("brief"))),
                "author_brief_available": bool(_text(draft.get("author_brief"))),
                "author_outline_available": bool(_text(draft.get("author_outline"))),
                "locked_requirement_count": len(locked_requirements),
            },
        },
        "artifacts": [
            _artifact_overview(
                stage,
                stages.get(stage) if isinstance(stages.get(stage), dict) else {},
                locks,
                revision,
            )
            for stage in STAGE_ORDER
        ],
        "retrieval_policy": (
            "This is an index only. Read one exact artifact or search the entity index; "
            "never assume omitted facts."
        ),
    }


def _selected_concept(draft: dict[str, Any]) -> dict[str, Any] | None:
    selected_concept_id = _text(draft.get("selected_concept_id"))
    concepts = draft.get("concepts") if isinstance(draft.get("concepts"), list) else []
    return next(
        (
            deepcopy(item)
            for item in concepts
            if isinstance(item, dict) and _text(item.get("id")) == selected_concept_id
        ),
        None,
    )


def _referenced_artifacts(draft: dict[str, Any]) -> dict[str, Any]:
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    references: dict[str, Any] = {}
    names = (
        draft.get("_context_artifacts")
        if isinstance(draft.get("_context_artifacts"), list)
        else []
    )
    for name in names:
        if name in {"constraints", "concepts"}:
            continue
        state = stages.get(name) if isinstance(stages.get(name), dict) else {}
        data = state.get("data") if isinstance(state.get("data"), dict) else None
        if data is None:
            continue
        collection_fields = [field for field, _kind in ENTITY_COLLECTIONS.get(name, ())]
        references[str(name)] = {
            "status": state.get("status"),
            "source": state.get("source"),
            "data": {
                key: deepcopy(value)
                for key, value in data.items()
                if key not in collection_fields
            },
            "omitted_collections": {
                field: len(data.get(field)) if isinstance(data.get(field), list) else 0
                for field in collection_fields
            },
        }
    return references


def build_stage_generation_context(
    draft: dict[str, Any],
    baseline: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Build the only project evidence rendered into one stage-generation prompt."""

    entity_target = (
        draft.get("_entity_target")
        if isinstance(draft.get("_entity_target"), dict)
        else None
    )
    return {
        "requirements": draft.get("form") if isinstance(draft.get("form"), dict) else {},
        "author_source": _author_context(draft),
        "selected_concept": _selected_concept(draft),
        "baseline": baseline,
        "refinement_instruction": _text(draft.get("_refinement_instruction")),
        "entity_target": entity_target,
        "retrieved_entities": deepcopy(
            draft.get("_retrieved_entities")
            if isinstance(draft.get("_retrieved_entities"), list)
            else []
        ),
        "referenced_artifacts": _referenced_artifacts(draft),
        "evidence_policy": (
            "Only baseline, selected_concept, retrieved_entities and explicit referenced_artifacts "
            "are available. Do not invent omitted project facts."
        ),
    }, entity_target


__all__ = [
    "artifact_data_shape",
    "build_stage_generation_context",
    "compact_creation_snapshot",
    "project_creation_artifact",
]
