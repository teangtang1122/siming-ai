"""Collect one streamed model response with native tool calls."""

from __future__ import annotations

from typing import Any


async def collect_tool_turn(gateway: Any, **kwargs: Any) -> dict[str, Any]:
    stream = gateway.stream_chat_completion_with_tools(**kwargs)
    if not hasattr(stream, "__aiter__"):
        close = getattr(stream, "close", None)
        if callable(close):
            close()
        raise TypeError(
            "stream_chat_completion_with_tools 必须返回异步事件流"
        )

    content: list[str] = []
    reasoning_content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, int] | None = None
    provider_state: list[dict[str, Any]] = []
    async for event in stream:
        event_type = event.get("type")
        if event_type == "content_delta":
            content.append(str(event.get("delta") or ""))
        elif event_type == "reasoning_delta":
            reasoning_content.append(str(event.get("delta") or ""))
        elif event_type == "tool_call_delta":
            index = int(event.get("index") or 0)
            call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if event.get("id"):
                call["id"] = str(event["id"])
            if event.get("name"):
                call["name"] = str(event["name"])
            if event.get("arguments_delta"):
                call["arguments"] += str(event["arguments_delta"])
        raw_usage = event.get("usage")
        if isinstance(raw_usage, dict) and raw_usage.get("prompt_tokens") is not None:
            usage = {
                key: max(0, int(raw_usage.get(key) or 0))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
        raw_provider_state = event.get("provider_state")
        if isinstance(raw_provider_state, list):
            provider_state = [
                dict(item)
                for item in raw_provider_state
                if isinstance(item, dict)
            ]
    tool_calls = [
        {
            # Preserve the provider's native identity exactly.  Missing IDs
            # are protocol failures for the caller; inventing one would make
            # an unverifiable tool result look executable.
            "id": call["id"],
            "type": "function",
            # Preserve the provider's argument stream exactly as well.  An
            # empty or malformed payload must reach the protocol validator and
            # fail the whole batch; silently repairing it to ``{}`` can change
            # the operation that is executed.
            "function": {"name": call["name"], "arguments": call["arguments"]},
        }
        for _, call in sorted(calls.items())
    ]
    return {
        "content": "".join(content),
        "reasoning_content": "".join(reasoning_content),
        "provider_state": provider_state,
        "tool_calls": tool_calls,
        "usage": usage,
    }


__all__ = ["collect_tool_turn"]
