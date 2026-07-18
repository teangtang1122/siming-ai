"""Small helpers for user-facing run event metadata.

The database stores run event details in payload_json. These helpers keep the
wire contract explicit without requiring a schema migration.
"""
from __future__ import annotations

import json
from typing import Any

from ...modules.operations.domain.failures import classify_failure


RUN_EVENT_META_FIELDS = (
    "model_source",
    "tool_mode",
    "failure_class",
    "checkpoint_id",
    "storage_target",
    "next_action",
)


def parse_payload_json(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        parsed = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return {"raw_payload": str(payload_json)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def merge_event_metadata(
    payload_json: str | None,
    *,
    event_type: str,
    status: str,
    message: str | None,
    **metadata: Any,
) -> str | None:
    """Merge explicit event metadata into payload_json.

    Empty values are ignored. Error events get a derived failure_class when the
    caller did not provide one.
    """
    payload = parse_payload_json(payload_json)
    for field in RUN_EVENT_META_FIELDS:
        value = metadata.get(field)
        if value not in (None, ""):
            payload[field] = str(value)
    if not payload.get("failure_class") and (status == "error" or event_type == "error"):
        failure_class = classify_failure(message)
        if failure_class:
            payload["failure_class"] = failure_class
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
