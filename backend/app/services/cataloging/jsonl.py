"""Helpers for forgiving JSONL parsing and candidate normalization."""
from __future__ import annotations

import json
from typing import Any

from .constants import VALID_ITEM_TYPES


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
    ):
        if key in raw and key not in normalized:
            normalized[key] = raw[key]
    return normalized


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
        return text
    aliases = {
        "summary": "chapter_summary",
        "chapter": "chapter_summary",
        "chapter_overview": "chapter_summary",
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
    }
    if text in aliases:
        item_type = aliases[text]
        if item_type == "outline_create" and op == "update":
            return "outline_update"
        return item_type
    return _infer_candidate_type(payload, op)


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
    if "name" in keys or "character_name" in keys or "target_name" in keys:
        if "event_description" in keys or "event" in keys:
            return "character_timeline"
        if keys & {
            "current_location",
            "current_goal",
            "life_status",
            "physical_state",
            "mental_state",
            "realm_or_level",
            "active_conflict",
            "abilities_state",
            "items_or_assets",
        }:
            return "character_state_update"
        if action in {"create", "new"}:
            return "character_create"
        return "character_update"
    if keys & {"role_type", "appearance", "personality", "background", "abilities", "tone_style", "catchphrases"}:
        return "character_create" if action in {"create", "new"} else "character_update"
    if keys & {
        "current_location",
        "current_goal",
        "life_status",
        "physical_state",
        "mental_state",
        "realm_or_level",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
    }:
        return "character_state_update"
    if "event_description" in keys and ("title" in keys or "entry_title" in keys or "dimension" in keys):
        return "worldbuilding_timeline"
    if keys & {"dimension", "category", "entry_title", "worldbuilding_title", "world_title", "setting_title"}:
        return "worldbuilding_update" if action == "update" else "worldbuilding_create"
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
            or payload.get("entry_title")
            or payload.get("worldbuilding_title")
            or payload.get("world_title")
            or payload.get("setting_title")
            or raw.get("target_name")
        )
        if title:
            payload["title"] = title
        if not payload.get("content"):
            payload["content"] = payload.get("description") or payload.get("event_description") or ""
        _normalize_dimension_alias(payload)
    if item_type.startswith("outline_") and not payload.get("title"):
        payload["title"] = raw.get("target_name") or payload.get("outline_title") or ""
    if item_type == "chapter_summary":
        summary = payload.get("summary_text") or payload.get("summary") or payload.get("content") or ""
        if summary:
            payload["summary_text"] = summary
            payload["summary"] = payload.get("summary") or summary
    payload["item_type"] = item_type
    payload["operation"] = operation
    payload["type"] = item_type
    payload["action"] = operation


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
