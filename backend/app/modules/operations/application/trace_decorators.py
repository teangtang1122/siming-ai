"""Explicit structural bindings for existing task and model entry points."""

from __future__ import annotations

import functools
import inspect
import json
from contextlib import nullcontext

from ..domain.context_trace import PAYLOAD_LIMIT, TraceScope
from .trace_capture import (
    Span,
    active_trace,
    correlate,
    record_payload,
    record_tool_outcome,
    trace_scope,
)


def _field(values: dict, path: str | None):
    if not path:
        return None
    value = values
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
    return str(value) if value is not None else None


def observed(
    *,
    kind: str,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    correlations: dict[str, str] | None = None,
    inputs: tuple[str, ...] = (),
    input_layer: str = "logical_request",
    output_layer: str = "adapter_output",
    label_field: str | None = None,
    attributes: dict[str, str] | None = None,
    capture_output: bool = True,
):
    def decorate(function):
        signature = inspect.signature(function)

        def contexts(args, kwargs):
            try:
                values = dict(signature.bind(*args, **kwargs).arguments)
                linked = {key: _field(values, path) for key, path in (correlations or {}).items()}
                identity = _field(values, scope_id)
                context = (
                    trace_scope(TraceScope(kind=scope_kind, id=identity), **linked)
                    if scope_kind and identity
                    else nullcontext()
                )
                return values, context
            except Exception:
                return {}, nullcontext()

        if inspect.isasyncgenfunction(function):

            @functools.wraps(function)
            async def stream(*args, **kwargs):
                values, context = contexts(args, kwargs)
                with context, Span(kind, function.__name__) as span:
                    record_payload(input_layer, {key: values.get(key) for key in inputs})
                    records = StreamRecords(output_layer)
                    try:
                        async for item in function(*args, **kwargs):
                            records.append(item)
                            _observe_stream_item(item, kind, span)
                            yield item
                    finally:
                        records.flush()

            return stream

        if not inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            def sync_call(*args, **kwargs):
                values, context = contexts(args, kwargs)
                with context, Span(kind, function.__name__):
                    record_payload(input_layer, {key: values.get(key) for key in inputs})
                    result = function(*args, **kwargs)
                    if capture_output:
                        record_payload(output_layer, result)
                    if isinstance(result, dict):
                        record_tool_outcome(result)
                    return result

            return sync_call

        @functools.wraps(function)
        async def call(*args, **kwargs):
            values, context = contexts(args, kwargs)
            with (
                context,
                Span(
                    kind,
                    _field(values, label_field) or function.__name__,
                    **{key: _field(values, path) for key, path in (attributes or {}).items()},
                ),
            ):
                record_payload(input_layer, {key: values.get(key) for key in inputs})
                result = await function(*args, **kwargs)
                if capture_output:
                    record_payload(output_layer, result)
                if isinstance(result, dict):
                    record_tool_outcome(result)
                return result

        return call

    return decorate


class StreamRecords:
    """Batch immutable deltas into 64 KiB events, capped at 16 MiB per model step."""

    def __init__(self, layer: str):
        self.layer, self.parts, self.size, self.total = layer, [], 0, 0
        self.trace = active_trace()
        self.stopped = not self.trace or self.trace.policy.mode != "full"
        if self.trace and self.stopped:
            record_payload(layer, None)

    def append(self, value):
        if self.stopped:
            return
        try:
            raw = self.trace.sink.encode(value)
            if raw is None or self.total + len(raw) > PAYLOAD_LIMIT or len(raw) > 65536:
                self.flush()
                self.trace.emit(
                    "payload",
                    {
                        "layer": self.layer,
                        "capture_source": "application",
                        "media_type": "application/json",
                        "completeness": "partial",
                        "missing_reason": "size_limit",
                    },
                )
                self.stopped = True
                return
            if self.size + len(raw) > 65536:
                self.flush()
            self.parts.append(raw)
            self.size += len(raw)
            self.total += len(raw)
        except Exception:
            self.trace.dropped += 1

    def flush(self):
        if self.parts:
            self.trace.emit(
                "payload",
                {
                    "layer": self.layer,
                    "capture_source": "application",
                    "media_type": "application/json",
                    "completeness": "complete",
                    "representation": "ordered_stream_deltas",
                },
                raw=b"[" + b",".join(self.parts) + b"]",
            )
            self.parts, self.size = [], 0


def _observe_stream_item(item, kind: str, span: Span) -> None:
    if not active_trace():
        return
    if kind != "turn" or not isinstance(item, str) or not item.startswith("data:"):
        return
    try:
        value = json.loads(item[5:].strip())
        data = value.get("data") or value
        correlate(
            **{
                key: data.get(key)
                for key in ("message_id", "assistant_message_id", "conversation_id", "run_id")
            }
        )
        if value.get("type") == "error":
            span.finish("error")
    except (ValueError, AttributeError, TypeError):
        pass
