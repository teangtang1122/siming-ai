"""Strict extraction for internal generators that require one native tool call."""

from __future__ import annotations

import json
from typing import Any


class NativeStructuredOutputError(ValueError):
    """A required native tool call did not satisfy the provider contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def required_tool_arguments(
    result: dict[str, Any],
    *,
    expected_name: str,
) -> tuple[dict[str, Any], str]:
    """Return exact JSON-object arguments from one complete native function call."""
    calls = result.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise NativeStructuredOutputError("required_native_tool_call_missing_or_ambiguous")
    call = calls[0]
    if not isinstance(call, dict):
        raise NativeStructuredOutputError("invalid_native_tool_call")
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise NativeStructuredOutputError("native_tool_call_id_missing")
    if call.get("type", "function") != "function":
        raise NativeStructuredOutputError("native_tool_call_type_invalid")
    function = call.get("function")
    if not isinstance(function, dict):
        raise NativeStructuredOutputError("native_tool_function_missing")
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        raise NativeStructuredOutputError("native_tool_name_missing")
    if name != expected_name:
        raise NativeStructuredOutputError("native_tool_name_unexpected")
    raw_arguments = function.get("arguments")
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        raise NativeStructuredOutputError("native_tool_arguments_missing")
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise NativeStructuredOutputError("native_tool_arguments_invalid_json") from exc
    if not isinstance(parsed, dict):
        raise NativeStructuredOutputError("native_tool_arguments_not_object")
    return parsed, raw_arguments


__all__ = ["NativeStructuredOutputError", "required_tool_arguments"]
