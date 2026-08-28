"""Validation, replay and compact telemetry for Creation Agent turns."""
from __future__ import annotations

import json
from typing import Any

CREATION_AGENT_TURN_SCHEMA = "creation_agent_turn.v1"
_MAX_REPLAY_TURNS = 6


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_count = sum(
        1 for char in text
        if "一" <= char <= "鿿" or "㐀" <= char <= "䶿"
    )
    return cjk_count + max(1, (len(text) - cjk_count) // 4)


def record_prompt_metric(
    captured: list[dict[str, Any]],
    *,
    iteration: int,
    phase: str,
    active_categories: tuple[str, ...],
    messages: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> None:
    raw_usage = result.get("usage") if isinstance(result, dict) else None
    usage = (
        raw_usage
        if isinstance(raw_usage, dict) and raw_usage.get("prompt_tokens") is not None
        else None
    )
    prompt_tokens = None
    if usage is not None:
        try:
            prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
        except (TypeError, ValueError):
            prompt_tokens = None
    system_content = ""
    if messages and messages[0].get("role") == "system":
        system_content = str(messages[0].get("content") or "")
    request_projection = json.dumps(
        {"messages": messages, "tools": schemas},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    captured.append({
        "iteration": iteration,
        "phase": phase,
        "active_categories": list(active_categories),
        "tool_count": len(schemas),
        "tool_schema_estimated_tokens": estimate_tokens(json.dumps(
            schemas,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )),
        "system_prompt_estimated_tokens": estimate_tokens(system_content),
        "request_estimated_tokens": estimate_tokens(request_projection),
        "prompt_tokens": prompt_tokens,
        "usage_reported": prompt_tokens is not None,
    })


def canonical_tool_call(
    value: Any,
    *,
    fallback_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    function = value.get("function")
    if not isinstance(function, dict):
        return None
    call_id = str(value.get("id") or fallback_id or "").strip()
    name = str(function.get("name") or "").strip()
    arguments = function.get("arguments")
    if not call_id or not name:
        return None
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {}, ensure_ascii=False, default=str)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _validated_turn_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return []
    normalized: list[dict[str, Any]] = []
    pending_tool_ids: set[str] = set()
    final_assistant_seen = False
    for index, raw_message in enumerate(value):
        if not isinstance(raw_message, dict) or final_assistant_seen:
            return []
        role = str(raw_message.get("role") or "")
        content = str(raw_message.get("content") or "")
        if index == 0:
            if role != "user" or not content.strip():
                return []
            normalized.append({"role": "user", "content": content[:1_000_000]})
            continue
        if role == "assistant":
            if pending_tool_ids:
                return []
            raw_calls = raw_message.get("tool_calls")
            if isinstance(raw_calls, list) and raw_calls:
                calls = [canonical_tool_call(call) for call in raw_calls]
                if any(call is None for call in calls):
                    return []
                canonical_calls = [call for call in calls if call is not None]
                call_ids = [str(call["id"]) for call in canonical_calls]
                if len(call_ids) != len(set(call_ids)):
                    return []
                pending_tool_ids = set(call_ids)
                normalized.append({
                    "role": "assistant",
                    "content": content[:80_000],
                    "tool_calls": canonical_calls,
                })
            else:
                if not content.strip():
                    return []
                normalized.append({"role": "assistant", "content": content[:80_000]})
                final_assistant_seen = True
            continue
        if role == "tool":
            tool_call_id = str(raw_message.get("tool_call_id") or "").strip()
            if tool_call_id not in pending_tool_ids:
                return []
            pending_tool_ids.remove(tool_call_id)
            normalized.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content[:120_000],
            })
            continue
        return []
    if pending_tool_ids or not final_assistant_seen:
        return []
    return normalized


def _conversation_projection(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay only human-visible conversation; live data must be read again."""

    if len(messages) < 2:
        return []
    first = messages[0]
    final = messages[-1]
    if first.get("role") != "user" or final.get("role") != "assistant":
        return []
    return [
        {"role": "user", "content": str(first.get("content") or "")[:20_000]},
        {"role": "assistant", "content": str(final.get("content") or "")[:8_000]},
    ]


def creation_agent_replay_messages(
    conversation: dict[str, Any] | None,
    *,
    session_id: str,
    exclude_assistant_message_id: str | None = None,
) -> list[dict[str, Any]]:
    turns: list[list[dict[str, Any]]] = []
    for message in (conversation or {}).get("messages") or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        if exclude_assistant_message_id and message.get("id") == exclude_assistant_message_id:
            continue
        payload = message.get("payload")
        trace = payload.get("creation_agent_turn") if isinstance(payload, dict) else None
        if not isinstance(trace, dict):
            continue
        if trace.get("schema") != CREATION_AGENT_TURN_SCHEMA:
            continue
        if str(trace.get("session_id") or "") != str(session_id):
            continue
        outcome = trace.get("outcome")
        if trace.get("replayable") is not True or not isinstance(outcome, dict):
            continue
        if outcome.get("status") != "completed":
            continue
        validated = _validated_turn_messages(trace.get("messages"))
        if validated:
            replayable = _conversation_projection(validated)
            if replayable:
                turns.append(replayable)
    return [message for turn in turns[-_MAX_REPLAY_TURNS:] for message in turn]


__all__ = [
    "CREATION_AGENT_TURN_SCHEMA",
    "canonical_tool_call",
    "creation_agent_replay_messages",
    "record_prompt_metric",
]
