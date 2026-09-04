"""Helpers for forgiving JSONL parsing and candidate normalization."""
from __future__ import annotations

import json
import re
from typing import Any

from ...modules.continuity.domain.cataloging_contract import (
    canonical_chapter_link_characters,
)
from ..story_granularity import (
    CHARACTER_STABLE_FIELDS,
    CHARACTER_STATE_FIELDS,
    NARRATIVE_STATE_FIELDS,
    SECTION_SCENE_STATE_FIELDS,
    derive_chapter_summary_text,
    extract_chapter_number,
    has_chapter_narrative_state,
    normalize_chapter_narrative_state,
    normalize_node_type,
    normalize_outline_payload,
)
from .constants import VALID_ITEM_TYPES


_LEDGER_WRAPPER_FIELDS = {
    "completed_beat": "events",
    "revealed_clue": "reader_known_facts",
    "storyline_state": "storyline_progress",
}
_WRAPPED_CANDIDATE_TYPES = set(VALID_ITEM_TYPES) | set(_LEDGER_WRAPPER_FIELDS) | {
    "narrative_promise",
}
_CHARACTER_RELATION_NAMES = {
    "allies_with",
    "brother_of",
    "child_of",
    "conflicts_with",
    "enemy_of",
    "father_of",
    "friend_of",
    "grandfather_of",
    "grandmother_of",
    "mother_of",
    "parent_of",
    "rival_of",
    "sibling_of",
    "sister_of",
}

_AGGREGATE_CANDIDATE_KEYS = {
    "chapter_summary",
    "chapter_outline",
    "character_state_updates",
    "character_updates",
    "worldbuilding_entries",
    "worldbuilding_updates",
    "outline_creates",
    "outline_updates",
    "chapter_links",
    "narrative_ledger",
}
_RESPONSE_COLLECTION_KEYS = ("candidates", "items", "results", "output", "response")
_CHARACTER_STATE_GUARD_FIELDS = {
    "background_before",
    "appearance_before",
    "appearance_evidence",
    "age_before",
    "age_evidence",
    "items_or_assets_before",
}
_CANDIDATE_ATTEMPT_MARKER = re.compile(
    r"(?m)^=== CANDIDATE RESOLUTION(?: RETRY \d+)? ===\s*$"
)
_JSON_FENCE = re.compile(r"```(?:jsonl?)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def expand_candidate_records(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a provider's one-object chapter archive into standard cards.

    The public contract asks for JSONL, but some API models return one valid
    JSON object whose values are candidate lists.  Treating that object as a
    single summary silently discarded every character, worldbuilding, outline
    and link entry.  This compatibility adapter only recognizes explicit
    aggregate field names and emits the same standard records that the normal
    JSONL path validates, deduplicates and applies.
    """

    if not isinstance(raw, dict) or not (set(raw) & _AGGREGATE_CANDIDATE_KEYS):
        return [raw]

    records: list[dict[str, Any]] = []
    explicit_type = next(
        (
            raw.get(key)
            for key in ("type", "item_type", "candidate_type", "kind", "card_type")
            if raw.get(key)
        ),
        None,
    )
    explicit_summary = False
    if explicit_type:
        # DeepSeek sometimes returns a valid chapter_summary and then appends
        # aggregate candidate arrays to that same object.  Preserve the typed
        # candidate itself, but expand the arrays into independent cards.
        primary = {
            key: value
            for key, value in raw.items()
            if key not in _AGGREGATE_CANDIDATE_KEYS
        }
        explicit_summary = _norm(str(explicit_type)) in {
            "chapter_summary",
            "summary",
            "chapter",
            "chapter_overview",
            "chapter_state",
            "chapter_narrative_state",
            "narrative_state",
            "章节摘要",
            "章节概览",
        }
        records.append(primary)

    summary = raw.get("chapter_summary")
    ledger_rows = _aggregate_rows(raw.get("narrative_ledger"))
    if explicit_summary and records:
        _merge_aggregate_ledger(records[0], ledger_rows)
    if isinstance(summary, dict):
        summary_record = {"type": "chapter_summary", **summary}
        narrative_overview = summary_record.get("narrative_state")
        if isinstance(narrative_overview, str):
            summary_record["narrative_overview"] = narrative_overview
            summary_record["narrative_state"] = {key: [] for key in NARRATIVE_STATE_FIELDS}
        narrative_review = summary_record.get("narrative_review")
        if isinstance(narrative_review, str):
            summary_record["narrative_review"] = {
                "source": "provided",
                "outcome": "assessed",
                "evidence": narrative_review,
            }
        _merge_aggregate_ledger(summary_record, ledger_rows)
        records.append(summary_record)

    chapter_outline = raw.get("chapter_outline")
    if isinstance(chapter_outline, dict):
        records.append({
            "type": "outline_create",
            "node_type": "chapter",
            **chapter_outline,
        })

    character_rows = _aggregate_rows(
        raw.get("character_state_updates"),
        raw.get("character_updates"),
    )
    for row in character_rows:
        item = _flatten_aggregate_data(row)
        operation = _norm(str(item.pop("operation", "update")))
        profile_payload = {
            key: value
            for key, value in item.items()
            if key in {*CHARACTER_STABLE_FIELDS, *CHARACTER_STATE_FIELDS}
            or key in _CHARACTER_STATE_GUARD_FIELDS
            or key in {"id", "character_name", "primary_name", "role_in_scene", "evidence"}
        }
        has_profile = any(
            profile_payload.get(key) not in (None, "", [], {})
            for key in CHARACTER_STABLE_FIELDS
        )
        has_state = any(
            profile_payload.get(key) not in (None, "", [], {})
            for key in CHARACTER_STATE_FIELDS
        )
        if has_profile or not has_state:
            records.append({
                "type": "character_create" if operation in {"create", "new"} else "character_update",
                "operation": operation,
                **profile_payload,
            })
        if has_state:
            records.append({
                "type": "character_state_update",
                "operation": "update",
                **profile_payload,
            })

    for row in _aggregate_rows(raw.get("worldbuilding_entries"), raw.get("worldbuilding_updates")):
        item = _flatten_aggregate_data(row)
        operation = _norm(str(item.pop("operation", "update")))
        records.append({
            "type": "worldbuilding_create" if operation in {"create", "new"} else "worldbuilding_update",
            "operation": operation,
            **item,
        })

    for row in _aggregate_rows(raw.get("outline_creates")):
        records.append({"type": "outline_create", **_flatten_aggregate_data(row)})
    for row in _aggregate_rows(raw.get("outline_updates")):
        records.append({"type": "outline_update", **_flatten_aggregate_data(row)})

    for row in _aggregate_rows(raw.get("chapter_links")):
        item = _flatten_aggregate_data(row)
        source_type = _norm(str(item.get("source_type") or ""))
        target_type = _norm(str(item.get("target_type") or ""))
        source_name = str(item.get("source_name") or item.get("source") or "").strip()
        target_name = str(item.get("target_name") or item.get("target") or "").strip()
        if source_name:
            item.setdefault("source", source_name)
        if target_name:
            item.setdefault("target", target_name)
        if source_type in {"character", "role"} and source_name:
            item.setdefault("character_names", [source_name])
        elif source_type in {"worldbuilding", "world", "setting"} and source_name:
            item.setdefault("worldbuilding_titles", [source_name])
        if target_type in {"outline", "outline_node", "chapter"} and target_name:
            item.setdefault("outline_title", target_name)
        item.setdefault("description", item.get("relation") or item.get("evidence") or "关联")
        if item.get("order") is not None and item.get("appearance_order") is None:
            item["appearance_order"] = item.get("order")
        records.append({"type": "chapter_link", **item})

    if not isinstance(summary, dict) and not explicit_summary:
        for row in ledger_rows:
            item = _flatten_aggregate_data(row)
            if item.get("type") or item.get("item_type"):
                records.append(item)

    return records or [raw]


def _aggregate_rows(*values: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _flatten_aggregate_data(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    data = item.pop("data", None)
    if isinstance(data, dict):
        return {**data, **item}
    return item


def _merge_aggregate_ledger(summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    state = summary.get("narrative_state")
    if not isinstance(state, dict):
        state = {}
    for row in rows:
        entry = _flatten_aggregate_data(row)
        semantic_type = _norm(str(entry.pop("type", entry.pop("item_type", ""))))
        canonical = {
            "completed_beat": "events",
            "revealed_clue": "reader_known_facts",
            "storyline_state": "storyline_progress",
        }.get(semantic_type)
        if semantic_type == "narrative_promise":
            is_resolution = str(entry.get("status") or "").strip().lower() in {
                "fulfilled",
                "resolved",
                "closed",
            }
            canonical = "foreshadowing_resolved" if is_resolution else "foreshadowing_planted"
            if is_resolution and not _has_stable_governance_reference(entry):
                entry["requested_status"] = entry.get("status")
                entry["status"] = "pending_review"
        if canonical:
            state.setdefault(canonical, []).append(entry)
    summary["narrative_state"] = state


def clean_jsonl_text(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def candidate_response_attempts(raw_output: str) -> list[str]:
    """Return candidate-generation attempts without fact-stage or marker text."""

    text = str(raw_output or "")
    matches = list(_CANDIDATE_ATTEMPT_MARKER.finditer(text))
    if not matches:
        return [text] if text.strip() else []
    attempts: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        if value:
            attempts.append(value)
    return attempts


def parse_candidate_response_records(text: str) -> list[dict[str, Any]]:
    """Parse JSONL, aggregate objects, arrays, fences, and pretty JSON alike."""

    value = str(text or "").strip().lstrip("\ufeff")
    if not value:
        return []

    fenced_records: list[dict[str, Any]] = []
    for block in _JSON_FENCE.findall(value):
        fenced_records.extend(_parse_candidate_response_value(block))
    if fenced_records:
        return fenced_records
    return _parse_candidate_response_value(clean_jsonl_text(value))


def _parse_candidate_response_value(text: str) -> list[dict[str, Any]]:
    try:
        parsed_records = _expand_response_value(json.loads(text))
        if parsed_records:
            return parsed_records
    except Exception:
        pass

    # raw_decode handles JSONL, concatenated objects, pretty JSON and mixtures
    # of prose plus JSON in one pass.  Parsing individual lines first could
    # return a partial set and silently ignore a later pretty-printed object.
    decoder = json.JSONDecoder()
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        positions = [position for position in (text.find("{", index), text.find("[", index)) if position >= 0]
        if not positions:
            break
        start = min(positions)
        try:
            parsed, end = decoder.raw_decode(text, start)
        except Exception:
            index = start + 1
            continue
        records.extend(_expand_response_value(parsed))
        index = max(end, start + 1)
    return records


def _expand_response_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        nested = value.strip().lstrip("\ufeff")
        if nested.startswith(("{", "[")):
            return _parse_candidate_response_value(nested)
        return []
    if isinstance(value, list):
        records: list[dict[str, Any]] = []
        for item in value:
            records.extend(_expand_response_value(item))
        return records
    if not isinstance(value, dict):
        return []

    has_explicit_type = any(
        value.get(key)
        for key in ("type", "item_type", "candidate_type", "kind", "card_type")
    )
    if not has_explicit_type:
        for key in _RESPONSE_COLLECTION_KEYS:
            collection = value.get(key)
            if isinstance(collection, (dict, list, str)):
                records = _expand_response_value(collection)
                if records:
                    return records
    return expand_candidate_records(value)


def parse_json_line(line: str) -> dict[str, Any] | None:
    text = line.strip().lstrip("\ufeff")
    if not text or text.startswith("//") or text.startswith("#"):
        return None
    if text.startswith("```") or text == "[DONE]":
        return None
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("JSONL line must be an object")
    return parsed


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_from_raw(raw)
    raw_type = _raw_type(raw, payload)
    action = _raw_action(raw, payload)
    item_type = _canonical_candidate_type(raw_type, action, payload)
    operation = _operation_for(item_type, action)
    _normalize_payload_fields(payload, raw, item_type, operation)
    return {
        "item_type": item_type,
        "operation": operation,
        "target_type": raw.get("target_type") or payload.get("target_type"),
        "target_id": raw.get("target_id") or payload.get("target_id") or payload.get("id"),
        "target_name": (
            raw.get("target_name")
            or payload.get("target_name")
            or payload.get("name")
            or payload.get("title")
            or payload.get("entry_title")
        ),
        "confidence": raw.get("confidence") or payload.get("confidence"),
        "evidence": raw.get("evidence") or payload.get("evidence"),
        "source_task": raw.get("source_task") or "chapter_cataloging",
        "payload": payload,
    }


def _payload_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    wrapped = raw.get("candidate")
    if isinstance(wrapped, dict):
        raw = wrapped
    wrapper_type, wrapper_payload = _single_candidate_wrapper(raw)
    if wrapper_payload is not None and not isinstance(raw.get("payload"), dict) and not isinstance(raw.get("data"), dict):
        return _normalize_wrapper_payload(wrapper_type, wrapper_payload)
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = raw.get("data")
    if not isinstance(payload, dict):
        payload = {
            k: v
            for k, v in raw.items()
            if k
            not in {
                "type",
                "item_type",
                "candidate_type",
                "kind",
                "card_type",
                "action",
                "operation",
                "payload",
                "data",
                "candidate",
                "target_type",
                "target_id",
                "target_name",
                "confidence",
                "source_task",
            }
        }
    normalized = dict(payload)
    for container_key in ("fields", "changes", "updates"):
        container = raw.get(container_key) or normalized.get(container_key)
        if isinstance(container, dict):
            normalized.update(container)
        elif isinstance(container, list):
            _merge_key_value_lines(normalized, container)
    for key in (
        "name",
        "character_name",
        "title",
        "entry_title",
        "summary",
        "summary_text",
        "content",
        "description",
        "dimension",
        "category",
        "aliases",
        "source_name",
        "target_name",
        "character_a",
        "character_b",
        "relationship_type",
        "source",
        "target",
        "from_name",
        "to_name",
        "chapter_id",
        "outline_node_id",
        "id",
        "target_id",
        "node_type",
        "parent_title",
        "related_characters",
        "event_description",
        "event",
        "event_type",
        "key_events",
        "character_names",
        "worldbuilding_titles",
        "worldbuilding_title",
        "world_title",
        "setting_title",
        "chapter_title",
        "primary_name",
        "secondary_name",
        "canonical_name",
        "confidence_reason",
        "evidence_points",
        "narrative_state",
        "narrative_review",
        "governance_candidates",
        "events",
        "timeline_events",
        "chapter_events",
        "foreshadowing_planted",
        "foreshadowing_resolved",
        "advanced_storylines",
        "storyline_progress",
        "new_storylines",
        "reader_known_facts",
        "character_known_facts",
        "unresolved_actions",
        "character_actions",
        "relationship_changes",
        "scene_number",
        "purpose",
        "location",
        "timeline",
        "pov_character",
        "characters",
        "entry_state",
        "exit_state",
        "emotional_residue",
        "locations",
        "items",
        "importance",
        "appearance_order",
        "beat",
        "clue",
        "promise",
        "storyline",
        "resolves_item_id",
        "resolves_dedupe_key",
        "governance_item_id",
        "dedupe_key",
        "resolution_evidence",
        "resolution_note",
    ):
        if key in raw and key not in normalized:
            normalized[key] = raw[key]
    return normalized


def _single_candidate_wrapper(raw: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Recognize model output shaped as ``{"candidate_type": {...}}``.

    Some local models follow the semantic JSON schema literally and use the
    candidate type as a single wrapper key instead of repeating it in a
    top-level ``type`` field.  Treating that valid shape as an unknown type
    caused narrative ledger and chapter-link data to disappear during
    cataloging retries.
    """

    matches: list[tuple[str, dict[str, Any]]] = []
    for key, value in raw.items():
        normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in _WRAPPED_CANDIDATE_TYPES and isinstance(value, dict):
            matches.append((normalized, value))
    if len(matches) != 1:
        return "", None
    return matches[0]


def _normalize_wrapper_payload(wrapper_type: str, value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    if wrapper_type in _LEDGER_WRAPPER_FIELDS or wrapper_type == "narrative_promise":
        label_key = {
            "completed_beat": "beat",
            "revealed_clue": "clue",
            "narrative_promise": "promise",
            "storyline_state": "storyline",
        }[wrapper_type]
        entry = dict(payload)
        label = entry.get(label_key)
        if label and not entry.get("title"):
            entry["title"] = label
        if wrapper_type == "narrative_promise":
            canonical = (
                "foreshadowing_resolved"
                if str(entry.get("status") or "").strip().lower() in {"fulfilled", "resolved", "closed"}
                else "foreshadowing_planted"
            )
            if canonical == "foreshadowing_resolved" and not _has_stable_governance_reference(entry):
                entry["requested_status"] = entry.get("status")
                entry["status"] = "pending_review"
        else:
            canonical = _LEDGER_WRAPPER_FIELDS[wrapper_type]
        normalized: dict[str, Any] = {canonical: [entry]}
        for key in ("chapter", "chapter_id", "chapter_title", "confidence", "evidence"):
            if payload.get(key) not in (None, ""):
                normalized[key] = payload[key]
        return normalized
    if wrapper_type == "worldbuilding_timeline":
        related = payload.get("related_worldbuilding")
        if not payload.get("title") and isinstance(related, list) and related:
            payload["title"] = related[0]
        if not payload.get("event_description"):
            payload["event_description"] = payload.get("description") or payload.get("event") or ""
    return payload


def _has_stable_governance_reference(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("resolves_item_id")
        or payload.get("governance_item_id")
        or payload.get("resolves_dedupe_key")
    )


def _normalize_typed_ledger_payload(payload: dict[str, Any], raw: dict[str, Any]) -> None:
    """Convert legacy top-level ledger candidate types into narrative state.

    The wrapper form (``{"completed_beat": {...}}``) was already supported,
    while API models commonly emit ``{"type":"completed_beat", ...}``.
    Canonical type mapping alone turned the latter into an empty
    ``chapter_summary`` and silently dropped it.  Normalize both shapes to the
    same chapter narrative-state contract before validation.
    """

    semantic_type = _norm(_raw_type(raw, payload))
    if semantic_type not in {*_LEDGER_WRAPPER_FIELDS, "narrative_promise"}:
        return
    if has_chapter_narrative_state(payload):
        return

    label_key = {
        "completed_beat": "beat",
        "revealed_clue": "clue",
        "narrative_promise": "promise",
        "storyline_state": "storyline",
    }[semantic_type]
    metadata_keys = {
        "type",
        "item_type",
        "candidate_type",
        "kind",
        "card_type",
        "action",
        "operation",
        "narrative_state",
        "narrative_review",
        "governance_candidates",
    }
    entry = {key: value for key, value in payload.items() if key not in metadata_keys}
    label = entry.get(label_key)
    if label and not entry.get("title"):
        entry["title"] = label

    if semantic_type == "narrative_promise":
        is_resolution = str(entry.get("status") or "").strip().lower() in {
            "fulfilled",
            "resolved",
            "closed",
        }
        canonical = "foreshadowing_resolved" if is_resolution else "foreshadowing_planted"
        if is_resolution and not _has_stable_governance_reference(entry):
            entry["requested_status"] = entry.get("status")
            entry["status"] = "pending_review"
    else:
        canonical = _LEDGER_WRAPPER_FIELDS[semantic_type]

    payload["narrative_state"] = {canonical: [entry]}
    payload.setdefault(
        "narrative_review",
        {
            "source": "provided",
            "outcome": "assessed",
            "evidence": f"模型提供了 {semantic_type} 叙事账本候选。",
        },
    )


def _merge_key_value_lines(payload: dict[str, Any], values: list[Any]) -> None:
    for value in values:
        if not isinstance(value, str):
            continue
        separator = "：" if "：" in value else ":" if ":" in value else ""
        if not separator:
            continue
        key, text = value.split(separator, 1)
        key = key.strip()
        text = text.strip()
        if key and text:
            payload[key] = text


def _raw_type(raw: dict[str, Any], payload: dict[str, Any]) -> str:
    value = (
        raw.get("type")
        or raw.get("item_type")
        or raw.get("candidate_type")
        or raw.get("kind")
        or raw.get("card_type")
        or raw.get("update_type")
        or raw.get("category_type")
        or payload.get("type")
        or payload.get("item_type")
        or payload.get("candidate_type")
        or payload.get("kind")
        or payload.get("card_type")
        or payload.get("update_type")
        or payload.get("category_type")
        or ""
    )
    if not value:
        wrapper_type, _ = _single_candidate_wrapper(raw)
        value = wrapper_type
    if not value:
        # A few API models put the candidate type in ``payload.node_type``
        # even though that field is reserved for outline granularity.  Accept
        # it only when it is an actual candidate type; values such as
        # chapter/section/volume must continue to mean outline node types.
        node_type_hint = _norm(str(payload.get("node_type") or ""))
        if node_type_hint in VALID_ITEM_TYPES:
            value = node_type_hint
    return str(value).strip()


def _raw_action(raw: dict[str, Any], payload: dict[str, Any]) -> str:
    value = (
        raw.get("operation")
        or raw.get("action")
        or payload.get("operation")
        or payload.get("action")
        or ""
    )
    return str(value or "upsert").strip().lower()


def _norm(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _canonical_candidate_type(raw_type: str, action: str, payload: dict[str, Any]) -> str:
    text = _norm(raw_type)
    op = _norm(action)
    if text in VALID_ITEM_TYPES:
        if text == "chapter_link" and _looks_like_character_relationship(payload):
            payload.setdefault("source_name", payload.get("source"))
            payload.setdefault("target_name", payload.get("target"))
            payload.setdefault("relationship_type", payload.get("relation"))
            return "character_relationship"
        return text
    aliases = {
        "summary": "chapter_summary",
        "chapter": "chapter_summary",
        "chapter_overview": "chapter_summary",
        "chapter_state": "chapter_summary",
        "chapter_narrative_state": "chapter_summary",
        "narrative_state": "chapter_summary",
        "章节摘要": "chapter_summary",
        "章节概览": "chapter_summary",
        "outline": "outline_create",
        "outline_node": "outline_create",
        "chapter_outline": "outline_create",
        "scene_outline": "outline_create",
        "大纲": "outline_create",
        "大纲节点": "outline_create",
        "new_character": "character_create",
        "create_character": "character_create",
        "character_new": "character_create",
        "character": "character_update" if op in {"update", "upsert", "merge"} else "character_create",
        "角色": "character_update" if op in {"update", "upsert", "merge"} else "character_create",
        "update_character": "character_update",
        "character_profile": "character_update",
        "character_card": "character_update",
        "角色档案": "character_update",
        "character_state": "character_state_update",
        "state": "character_state_update",
        "character_status": "character_state_update",
        "角色状态": "character_state_update",
        "relationship": "character_relationship",
        "relation": "character_relationship",
        "character_relation": "character_relationship",
        "relationship_update": "character_relationship",
        "角色关系": "character_relationship",
        "timeline": "character_timeline",
        "character_event": "character_timeline",
        "character_timeline_event": "character_timeline",
        "角色时间线": "character_timeline",
        "character_merge": "character_merge_candidate",
        "duplicate_character": "character_merge_candidate",
        "merge_character": "character_merge_candidate",
        "角色合并": "character_merge_candidate",
        "new_worldbuilding": "worldbuilding_create",
        "create_worldbuilding": "worldbuilding_create",
        "worldbuilding": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "worldbuilding_entry": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "world": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "setting": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "lore": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "设定": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "世界观": "worldbuilding_update" if op in {"update", "upsert"} else "worldbuilding_create",
        "update_worldbuilding": "worldbuilding_update",
        "worldbuilding_event": "worldbuilding_timeline",
        "world_timeline": "worldbuilding_timeline",
        "setting_timeline": "worldbuilding_timeline",
        "世界观时间线": "worldbuilding_timeline",
        "link": "chapter_link",
        "chapter_link": "chapter_link",
        "章节关联": "chapter_link",
        "completed_beat": "chapter_summary",
        "revealed_clue": "chapter_summary",
        "narrative_promise": "chapter_summary",
        "storyline_state": "chapter_summary",
    }
    if text in aliases:
        item_type = aliases[text]
        if item_type == "outline_create" and op == "update":
            return "outline_update"
        return item_type
    return _infer_candidate_type(payload, op)


def _looks_like_character_relationship(payload: dict[str, Any]) -> bool:
    if not payload.get("source") or not payload.get("target"):
        return False
    relation = _norm(str(payload.get("relationship_type") or payload.get("relation") or ""))
    if not relation:
        return False
    return (
        relation in _CHARACTER_RELATION_NAMES
        or relation.endswith("_of")
        or any(marker in relation for marker in ("亲属", "父子", "父女", "母子", "母女", "兄弟", "姐妹", "敌对", "冲突"))
    )


def _infer_candidate_type(payload: dict[str, Any], action: str) -> str:
    keys = {str(key) for key in payload}
    if {"primary_name", "secondary_name"} <= keys:
        return "character_merge_candidate"
    if (
        {"source_name", "target_name"} <= keys
        or {"character_a", "character_b"} <= keys
        or ("relationship_type" in keys and keys & {"source", "target", "from_name", "to_name"})
    ):
        return "character_relationship"
    if "character_names" in keys or "worldbuilding_titles" in keys or "outline_title" in keys:
        return "chapter_link"
    state_keys = set(CHARACTER_STATE_FIELDS)
    # Prefer explicit domain fields over the generic ``name`` key.  Otherwise
    # malformed-but-recoverable worldbuilding cards such as
    # {name, dimension, description} become characters.
    if keys & {"dimension", "category", "entry_title", "worldbuilding_title", "world_title", "setting_title"}:
        if "event_description" in keys and keys & {"title", "entry_title", "name"}:
            return "worldbuilding_timeline"
        return "worldbuilding_update" if action == "update" else "worldbuilding_create"
    # Scene fields are stronger evidence than narrative-state fields.  Some
    # providers include entry/exit state on an outline scene, which previously
    # caused it to be misclassified as a chapter summary.
    if "parent_title" in keys or "related_characters" in keys or "scene_number" in keys:
        return "outline_update" if action == "update" else "outline_create"
    if keys & set(SECTION_SCENE_STATE_FIELDS):
        return "outline_update" if action == "update" else "outline_create"
    if "name" in keys or "character_name" in keys or "target_name" in keys:
        if "event_description" in keys or "event" in keys:
            return "character_timeline"
        if keys & state_keys:
            return "character_state_update"
        if action in {"create", "new"}:
            return "character_create"
        return "character_update"
    if keys & {"role_type", "appearance", "personality", "background", "abilities", "tone_style", "catchphrases"}:
        return "character_create" if action in {"create", "new"} else "character_update"
    if keys & state_keys:
        return "character_state_update"
    narrative_keys = set(NARRATIVE_STATE_FIELDS) | {
        "narrative_state",
        "chapter_events",
        "advanced_storylines",
        "revealed_facts",
        "facts_reader_known",
        "facts_character_known",
    }
    if keys & narrative_keys:
        return "chapter_summary"
    if "event_description" in keys and ("title" in keys or "entry_title" in keys or "dimension" in keys):
        return "worldbuilding_timeline"
    if "node_type" in keys or "parent_title" in keys or "related_characters" in keys:
        return "outline_update" if action == "update" else "outline_create"
    if "summary_text" in keys or "key_events" in keys:
        return "chapter_summary"
    if "title" in keys and "summary" in keys:
        return "outline_update" if action == "update" else "outline_create"
    if "summary" in keys and not keys & {"content", "dimension", "category"}:
        return "chapter_summary"
    if "title" in keys and "content" in keys:
        return "worldbuilding_update" if action == "update" else "worldbuilding_create"
    return "unknown"


def _operation_for(item_type: str, action: str) -> str:
    op = _norm(action)
    if item_type.endswith("_create"):
        return "create"
    if item_type.endswith("_update") or item_type in {"character_state_update", "worldbuilding_timeline"}:
        return "update"
    if item_type == "character_merge_candidate":
        return "merge"
    if item_type == "chapter_link":
        return "link"
    if op in {"create", "update", "delete", "merge", "link", "upsert"}:
        return op
    return "upsert"


def _normalize_payload_fields(
    payload: dict[str, Any],
    raw: dict[str, Any],
    item_type: str,
    operation: str,
) -> None:
    if item_type == "chapter_summary":
        _normalize_typed_ledger_payload(payload, raw)
    target_id = payload.get("id") or payload.get("target_id") or raw.get("target_id")
    if target_id:
        payload["id"] = target_id
        payload["target_id"] = target_id
    if item_type.startswith("character_") and item_type != "character_relationship":
        name = payload.get("name") or payload.get("character_name") or raw.get("target_name")
        if name:
            payload["name"] = name
    if item_type == "character_relationship":
        if not payload.get("source_name"):
            payload["source_name"] = payload.get("source") or payload.get("from_name")
        if not payload.get("target_name"):
            payload["target_name"] = payload.get("target") or payload.get("to_name")
        if not payload.get("source_name") and payload.get("character_a"):
            payload["source_name"] = payload.get("character_a")
        if not payload.get("target_name") and payload.get("character_b"):
            payload["target_name"] = payload.get("character_b")
    if item_type.startswith("worldbuilding_"):
        title = (
            payload.get("title")
            or payload.get("name")
            or payload.get("entry_title")
            or payload.get("worldbuilding_title")
            or payload.get("world_title")
            or payload.get("setting_title")
            or raw.get("target_name")
        )
        if title:
            payload["title"] = title
        if not payload.get("content"):
            payload["content"] = (
                payload.get("description")
                or payload.get("event_description")
                or payload.get("significance")
                or ""
            )
        _normalize_dimension_alias(payload)
    if item_type == "chapter_link":
        payload["characters"] = canonical_chapter_link_characters(payload)
        payload.pop("character_names", None)
    if item_type.startswith("outline_"):
        raw_parent_id = str(payload.get("parent_id") or "").strip()
        if raw_parent_id and not _looks_like_uuid(raw_parent_id):
            payload.setdefault("parent_title", raw_parent_id)
            payload.pop("parent_id", None)
        candidate_type_hint = _norm(str(payload.get("node_type") or ""))
        if candidate_type_hint in VALID_ITEM_TYPES:
            # Restore the semantic outline granularity after consuming the
            # provider's misplaced candidate type.
            payload["node_type"] = (
                "section"
                if payload.get("scene_number") is not None
                or payload.get("parent_title")
                or set(payload.keys()) & set(SECTION_SCENE_STATE_FIELDS)
                else "chapter"
            )
        if not payload.get("title"):
            payload["title"] = (
                raw.get("target_name")
                or payload.get("outline_title")
                or payload.get("name")
                or payload.get("heading")
                or payload.get("chapter_title")
                or ""
            )
        if not payload.get("node_type") and set(payload.keys()) & set(SECTION_SCENE_STATE_FIELDS):
            payload["node_type"] = "section"
        payload["node_type"] = normalize_node_type(payload.get("node_type"))
        payload.update(normalize_outline_payload(
            payload,
            chapter_number=extract_chapter_number(
                payload.get("title"),
                payload.get("chapter_title"),
                raw.get("target_name"),
                raw.get("chapter_title"),
            ),
        ))
    if item_type == "chapter_summary":
        summary = derive_chapter_summary_text(payload)
        if summary:
            payload["summary_text"] = summary
            payload["summary"] = payload.get("summary") or summary
        if has_chapter_narrative_state(payload):
            payload["narrative_state"] = normalize_chapter_narrative_state(payload)
        scenes = payload.get("scenes")
        if isinstance(scenes, list) and "scene_count" not in payload:
            payload["scene_count"] = len(scenes)
    payload["item_type"] = item_type
    payload["operation"] = operation
    payload["type"] = item_type
    payload["action"] = operation


def _looks_like_uuid(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) != 36:
        return False
    parts = text.split("-")
    return [len(part) for part in parts] == [8, 4, 4, 4, 12] and all(
        all(char in "0123456789abcdefABCDEF" for char in part)
        for part in parts
    )


def _normalize_dimension_alias(payload: dict[str, Any]) -> None:
    category = str(payload.get("dimension") or payload.get("category") or "").strip().lower()
    if category in {"creature", "species", "race", "妖兽", "生物", "种族"}:
        payload["dimension"] = "races"
    elif category in {"item", "technique", "artifact", "magic", "power", "cultivation", "物品", "技术", "功法", "修炼", "规则"}:
        payload["dimension"] = "power_system"
    elif category in {"location", "place", "geography", "地点", "地理", "区域"}:
        payload["dimension"] = "geography"
    elif category in {"faction", "organization", "sect", "势力", "组织", "宗门", "门派", "家族"}:
        payload["dimension"] = "factions"
