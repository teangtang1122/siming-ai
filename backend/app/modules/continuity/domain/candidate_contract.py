"""Candidate field schemas shared by native Agent tools and the transactional applier.

These describe structure only. Archive identity and source-evidence checks stay
at the transactional write boundary; invalid values are never guessed/coerced.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.architecture.tool_spec import _validate_exported_schema

MANAGED_CATALOGING_MAX_CANDIDATES = 3


def _object(properties, required=()):
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = list(required)
    return result


def _strings():
    return {"type": "array", "items": {"type": "string"}}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string"}
RELATIONSHIP = _object(
    {
        "source_name": TEXT,
        "target_name": TEXT,
        "relationship_type": TEXT,
    },
    ("source_name", "target_name", "relationship_type"),
)
MANIFEST = _object(
    {
        "scene_count": {"type": "integer", "minimum": 1},
        "characters": _strings(),
        "worldbuilding": _strings(),
        "relationships": {"type": "array", "items": RELATIONSHIP},
        "character_profiles": _strings(),
    },
    ("scene_count", "characters", "worldbuilding", "relationships", "character_profiles"),
)
CHARACTER_LINK = _object(
    {
        "name": {"type": "string", "minLength": 1},
        "appearance_type": _enum("出场", "提及", "回忆"),
    },
    ("name", "appearance_type"),
)

_PROFILE = {
    **{
        name: TEXT
        for name in (
            "name",
            "appearance",
            "age",
            "personality",
            "background",
            "background_before",
            "tone_style",
            "emotion_tendency",
            "custom_system_prompt",
        )
    },
    "role_type": _enum("protagonist", "supporting", "antagonist", "mentor", "other"),
    "aliases": _strings(),
    "abilities": _strings(),
    "catchphrases": _strings(),
    "verbosity": _enum("brief", "moderate", "verbose"),
    "profile": _object(
        {
            **{
                name: TEXT
                for name in (
                    "core_motivation",
                    "inner_lack",
                    "core_belief",
                    "public_persona",
                    "hidden_persona",
                    "moral_taboo",
                    "voice",
                    "action_habit",
                    "trauma_trigger",
                )
            },
            "reveal_chapter": {"type": ["integer", "null"]},
        }
    ),
}
_STATE = {
    **{
        name: TEXT
        for name in (
            "name",
            "age",
            "appearance",
            "current_location",
            "realm_or_level",
            "physical_state",
            "mental_state",
            "current_goal",
            "active_conflict",
            "abilities_state",
            "items_or_assets",
            "appearance_before",
            "appearance_evidence",
            "age_before",
            "age_evidence",
            "items_or_assets_before",
        )
    },
    "aliases": _strings(),
    "life_status": _enum("alive", "dead", "unknown"),
}
_WORLD = {
    "title": TEXT,
    "content": TEXT,
    "dimension": _enum("geography", "history", "factions", "power_system", "races", "culture"),
    "source_labels": _strings(),
    "client_id": TEXT,

}
_OUTLINE = {
    "client_id": {
        "type": "string",
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    },
    **{
        name: TEXT
        for name in (
            "title",
            "summary",
            "actual_summary",
            "planned_summary",
            "parent_id",
            "purpose",
            "location",
            "timeline",
            "pov_character",
            "entry_state",
            "exit_state",
            "emotional_residue",
        )
    },
    "node_type": _enum("chapter", "section", "volume"),
    "status": _enum("pending", "in_progress", "completed"),
    "scene_number": {"type": "integer", "minimum": 1},
    "characters": _strings(),
    "character_ids": {**_strings(), "uniqueItems": True},
    "unresolved_actions": {"type": "array"},
}

CANDIDATE_FIELDS = {
    "chapter_summary": {
        "scenes": {"type": "array", "minItems": 1, "items": TEXT},
        **{key: {"type": "array", "items": _object({
            "name": {"type": "string", "minLength": 1},
            "id": {
                "type": "string",
                "description": "Existing ID, or a new canonical UUID reused as client_id.",
            },
            "decision": _enum("existing", "new"),
            "source_labels": _strings(),
            "reason": {"type": "string", "minLength": 1},
        }, ("name", "id", "decision", "reason"))}
           for key in ("character_bindings", "worldbuilding_bindings")},
        "summary_text": TEXT,
        "key_events": _strings(),
        "characters": _strings(),
        "worldbuilding": _strings(),
        "coverage_manifest": MANIFEST,
        "coverage_manifest_mode": _enum("replace"),
        "narrative_state": _object(
            {
                name: {"type": "array"}
                for name in (
                    "events",
                    "timeline_events",
                    "foreshadowing_planted",
                    "foreshadowing_resolved",
                    "storyline_progress",
                    "new_storylines",
                    "reader_known_facts",
                    "character_known_facts",
                    "unresolved_actions",
                    "character_actions",
                    "relationship_changes",
                )
            }
        ),
        "narrative_review": {"type": "object"},
        "governance_candidates": {"type": "array", "items": {"type": "object"}},
    },
    "outline_create": _OUTLINE,
    "outline_update": _OUTLINE,
    "character_create": {
        **_PROFILE,
        **_STATE,
        "client_id": {
            "type": "string",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            "description": "Unused canonical UUID for the new character and outline references.",
        },
    },
    "character_update": _PROFILE,
    "character_state_update": _STATE,
    "character_timeline": {"name": TEXT, "event_description": TEXT, "emotional_state_change": TEXT},
    "character_relationship": {**RELATIONSHIP["properties"], "description": TEXT},
    "character_merge_candidate": {"primary_name": TEXT, "secondary_name": TEXT, "reason": TEXT,
                                  "aliases": _strings(), "background_append": TEXT},
    "worldbuilding_create": _WORLD,
    "worldbuilding_update": _WORLD,
    "worldbuilding_timeline": {**_WORLD, "event_description": TEXT},
    "chapter_link": {
        "characters": {"type": "array", "items": CHARACTER_LINK},
        **{name: _strings() for name in ("worldbuilding_titles", "locations", "items", "events")},
        "outline_title": TEXT,
        "chapter_link_mode": _enum("replace"),
        "importance": _enum("major", "normal", "minor"),
        "appearance_order": {"type": "integer", "minimum": 1},
    },
}

_REQUIRED_FIELDS = {
    "chapter_summary": (
        "summary_text", "coverage_manifest", "scenes", "character_bindings",
        "worldbuilding_bindings", "narrative_state", "narrative_review",
    ),
    "character_create": ("name", "client_id"),
    "character_update": ("id",),
    "character_state_update": ("id",),
    "character_timeline": ("id",),
    "worldbuilding_create": ("title", "dimension", "client_id"),
    "worldbuilding_update": ("id", "title"),
    "worldbuilding_timeline": ("id", "title", "event_description"),
    "outline_create": ("title", "node_type", "summary", "character_ids"),
    "outline_update": ("id", "title", "node_type", "summary", "character_ids"),
    "character_relationship": ("source_name", "target_name", "relationship_type"),
    "character_merge_candidate": ("primary_name", "secondary_name"),
}


def candidate_payload_schema(item_type: str) -> dict[str, Any]:
    properties = {
        "id": TEXT,
        "type": _enum(item_type),
        "description": TEXT,
        "event_type": TEXT,
        "sort_order": {"type": "integer", "minimum": 0},
        "change_summary": TEXT,
        "evidence": TEXT,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        **CANDIDATE_FIELDS.get(item_type, {}),
    }
    return deepcopy(_object(properties))


def candidate_record_schema() -> dict[str, Any]:
    variants = []
    for item_type in CANDIDATE_FIELDS:
        schema = candidate_payload_schema(item_type)
        schema["properties"]["type"] = _enum(item_type)
        schema["required"] = ["type", *_REQUIRED_FIELDS.get(item_type, ())]
        schema["description"] = (
            "One aggregate chapter link per chapter; use native arrays, "
            "and amend only missing links."
            if item_type == "chapter_link"
            else item_type
        )
        variants.append(schema)
    variants.append(
        _object(
            {
                "type": _enum("scene_outline_replace"),
                "expected_candidate_ids": _strings(),
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": _object(
                        {
                            **_OUTLINE,
                            "type": _enum("outline_create"),
                            "node_type": _enum("section"),
                        },
                        ("type", "node_type", "scene_number", "title", "summary", "character_ids"),
                    ),
                },
            },
            ("type", "expected_candidate_ids", "sections"),
        )
    )
    return {"anyOf": variants}


def validate_candidate_fields(item_type: str, payload: dict[str, Any]) -> None:
    """Validate supplied fields before normalization can discard malformed data."""
    schema = candidate_payload_schema(item_type)
    schema["required"] = list(_REQUIRED_FIELDS.get(item_type, ()))
    _validate_exported_schema(schema, payload)
    if item_type == "chapter_link":
        from .cataloging_contract import canonical_chapter_link_characters
        canonical_chapter_link_characters(payload)


def candidate_contract_examples() -> list[dict[str, Any]]:
    """Small native JSON examples; placeholders are never executed automatically."""
    return [
        {
            "type": "chapter_summary",
            "summary_text": "本章已确认的事件摘要",
            "scenes": ["本章场景"],
            "character_bindings": [], "worldbuilding_bindings": [],
            "coverage_manifest": {
                "scene_count": 1,
                "characters": [],
                "worldbuilding": [],
                "relationships": [],
                "character_profiles": [],
            },
            "narrative_state": {
                key: []
                for key in CANDIDATE_FIELDS["chapter_summary"]["narrative_state"]["properties"]
            },
            "narrative_review": {"source": "provided", "outcome": "assessed"},
        },
        {
            "type": "outline_create",
            "title": "当前章节原题",
            "node_type": "chapter",
            "summary": "本章实际事件",
            "character_ids": [],
        },
        {
            "type": "chapter_link",
            "characters": [{"name": "已确认的角色主名", "appearance_type": "出场"}],
            "worldbuilding_titles": ["已确认的设定稳定标题"],
            "locations": [],
            "items": [],
            "events": [],
        },
    ]
