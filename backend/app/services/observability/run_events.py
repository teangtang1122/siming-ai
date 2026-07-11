"""Small helpers for user-facing run event metadata.

The database stores run event details in payload_json. These helpers keep the
wire contract explicit without requiring a schema migration.
"""
from __future__ import annotations

import json
import re
from typing import Any


RUN_EVENT_META_FIELDS = (
    "model_source",
    "tool_mode",
    "failure_class",
    "checkpoint_id",
    "storage_target",
    "next_action",
)


def classify_failure(message: str | None) -> str | None:
    """Classify common failures so the UI can show a concrete next step."""
    text = str(message or "").strip()
    if not text:
        return None
    lower = text.lower()
    if re.search(r"free\s+usage\s+exceeded|quota|rate\s*limit|too many requests|429|402", lower):
        return "quota_or_rate_limit"
    if re.search(r"invalidtoken|invalid[_\s-]*token|expired[_\s-]*token|401|unauthori[sz]ed|login|required", lower):
        return "auth"
    if "timeout" in lower or "超时" in text or "请求超时" in text:
        return "timeout"
    if "没有收到模型的文字回复" in text or "empty response" in lower or "no text" in lower:
        return "empty_response"
    if "only `read` tool" in lower or "工具均未注册" in text or "tool" in lower and "not registered" in lower:
        return "tool_unavailable"
    if "未入库" in text or "orphan" in lower or "mirror" in lower and "database" in lower:
        return "storage_contract"
    return "unknown"


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

