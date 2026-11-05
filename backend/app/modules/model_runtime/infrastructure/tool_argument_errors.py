"""Retain malformed arguments without repairing or authorizing a native call."""
from __future__ import annotations

import json

from app.core.exceptions import LLMToolArgumentsError


def completed_tool_argument_error(events: list[dict]) -> LLMToolArgumentsError | None:
    buffers: dict[int, dict] = {}
    for event in events:
        call = buffers.setdefault(int(event.get("index") or 0), {
            "id": "", "type": "function", "function": {"name": "", "arguments": ""},
        })
        if event.get("id"):
            call["id"] = str(event["id"])
        if event.get("name"):
            call["function"]["name"] = str(event["name"])
        call["function"]["arguments"] += str(event.get("arguments_delta") or "")
    calls = [call for _, call in sorted(buffers.items())]
    # Missing or duplicate identities cannot receive trustworthy native receipts.
    if not calls or any(not call["id"] or not call["function"]["name"] for call in calls):
        return None
    if len({call["id"] for call in calls}) != len(calls):
        return None
    errors = []
    for call in calls:
        function = call["function"]
        try:
            value = json.loads(function["arguments"])
            detail = "arguments 必须是 JSON 对象" if not isinstance(value, dict) else ""
        except json.JSONDecodeError as error:
            detail = (
                f"arguments JSON 无效（第 {error.lineno} 行，第 {error.colno} 列）：{error.msg}"
            )
        if detail:
            errors.append({"tool": function["name"], "call_id": call["id"],
                           "message": f"{function['name']}：{detail}"})
    if not errors:
        return None
    return LLMToolArgumentsError({"role": "assistant", "tool_calls": calls}, errors)
