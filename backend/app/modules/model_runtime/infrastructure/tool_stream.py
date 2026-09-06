"""Shared resume/validation helpers for one provider tool-stream attempt.

Kept in their own module so the LLM gateway stays under the architecture
module-size limit.  These helpers are pure provider-protocol utilities: they
consume adapter SSE events, validate that a native tool batch completed, and
re-emit the final ``done`` event with cumulative usage.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass

from app.ai.capabilities import request_meta
from app.core.exceptions import LLMError


class _ResumeHandshakeError(LLMError):
    """The replacement stream did not prove that it starts at our checkpoint."""


@dataclass
class _ResumeHandshake:
    expected_prefix: str
    buffered: str = ""
    verified: bool = False

    def consume(self, chunk: str) -> str:
        if self.verified:
            return chunk
        self.buffered += chunk
        candidate = self.buffered.lstrip()
        if len(candidate) < len(self.expected_prefix):
            if not self.expected_prefix.startswith(candidate):
                raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
            return ""
        if not candidate.startswith(self.expected_prefix):
            raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
        self.verified = True
        suffix = candidate[len(self.expected_prefix):]
        self.buffered = ""
        return suffix

    def require_verified(self) -> None:
        if not self.verified:
            raise _ResumeHandshakeError("模型恢复响应在检查点握手完成前结束")


def _tool_delta_events_complete(events: list[dict]) -> bool:
    if not events:
        return True
    calls: dict[int, dict[str, str]] = {}
    for event in events:
        index = int(event.get("index") or 0)
        call = calls.setdefault(index, {"name": "", "arguments": ""})
        if event.get("name"):
            call["name"] = str(event["name"])
        if event.get("arguments_delta"):
            call["arguments"] += str(event["arguments_delta"])
    for call in calls.values():
        if not call["name"]:
            return False
        if not call["arguments"]:
            return False
        try:
            arguments = json.loads(call["arguments"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(arguments, dict):
            return False
    return True


def _validate_tool_stream_completion(
    done_event: dict | None,
    buffered_tool_events: list[dict],
    handshake: _ResumeHandshake | None,
    content_seen: bool,
    usage_totals: dict[str, int],
    has_usage: bool,
) -> bool:
    if handshake and content_seen:
        handshake.require_verified()
    if done_event is None:
        raise _ResumeHandshakeError("模型工具流在正式结束帧到达前停止")
    raw_usage = done_event.get("usage")
    if isinstance(raw_usage, dict):
        has_usage = True
        for key in usage_totals:
            usage_totals[key] += max(0, int(raw_usage.get(key) or 0))
    finish_reason = str(done_event.get("finish_reason") or "").lower()
    if finish_reason in {"length", "max_tokens", "token_limit", "incomplete"}:
        raise _ResumeHandshakeError("模型输出达到单次长度上限，正在从检查点继续")
    if not _tool_delta_events_complete(buffered_tool_events):
        raise _ResumeHandshakeError("工具调用在参数完整前结束")
    if handshake and not content_seen and not buffered_tool_events:
        handshake.require_verified()
    return has_usage


async def _consume_tool_stream_attempt(
    *,
    generator: AsyncGenerator[dict, None],
    handshake: _ResumeHandshake | None,
    committed_parts: list[str],
    usage_totals: dict[str, int],
    has_usage_tracker: list[bool],
    received_tracker: list[bool],
    provider: str,
    model_name: str,
    notes: list[str],
    resume_attempt: int,
    timeout_seconds: int | None,
) -> AsyncGenerator[dict, None]:
    """Consume one adapter tool stream and yield its caller-visible events.

    Content deltas are emitted live, tool deltas are buffered until the provider
    closes the batch, and the final ``done`` event is re-emitted with cumulative
    usage.  Any provider/validation failure propagates to the retry loop.
    """
    content_seen = False
    buffered_tool_events: list[dict] = []
    done_event: dict | None = None
    while True:
        try:
            if timeout_seconds is None:
                chunk = await generator.__anext__()
            else:
                chunk = await asyncio.wait_for(generator.__anext__(), timeout=timeout_seconds)
        except StopAsyncIteration:
            break
        received_tracker[0] = True
        event_type = chunk.get("type")
        if event_type == "content_delta":
            delta = str(chunk.get("delta") or "")
            content_seen = content_seen or bool(delta)
            outgoing = handshake.consume(delta) if handshake else delta
            if outgoing:
                committed_parts.append(outgoing)
                yielded = dict(chunk)
                yielded["delta"] = outgoing
                yield yielded
        elif event_type == "tool_call_delta":
            buffered_tool_events.append(dict(chunk))
        elif event_type == "done":
            done_event = dict(chunk)
            with suppress(Exception):
                await generator.aclose()
            break
        else:
            yield chunk

    has_usage_tracker[0] = _validate_tool_stream_completion(
        done_event,
        buffered_tool_events,
        handshake,
        content_seen,
        usage_totals,
        has_usage_tracker[0],
    )
    for tool_event in buffered_tool_events:
        yield tool_event
    if done_event is not None:
        if has_usage_tracker[0]:
            done_event["usage"] = dict(usage_totals)
        if resume_attempt:
            notes.append(f"流式响应已从检查点续传 {resume_attempt} 次")
        done_event.setdefault("request_meta", request_meta(provider, model_name, notes))
        yield done_event


__all__ = [
    "_ResumeHandshake",
    "_ResumeHandshakeError",
    "_consume_tool_stream_attempt",
    "_tool_delta_events_complete",
    "_validate_tool_stream_completion",
]
