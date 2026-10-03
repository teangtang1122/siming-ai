"""Failure-isolated observation port and task-local trace/span correlation."""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
import uuid
from typing import Any, Protocol

from ..domain.context_trace import TRACE_SCHEMA, TracePolicy, TraceScope


class TraceSink(Protocol):
    def policy(self, owner: str) -> TracePolicy: ...
    def submit(
        self, record: dict, raw: bytes | None = None, secrets: tuple[str, ...] = ()
    ) -> bool: ...


_sink: TraceSink | None = None
request_identity: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "trace_request", default=None
)
_active: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "context_trace", default=None
)
_parent: contextvars.ContextVar[str | None] = contextvars.ContextVar("context_span", default=None)


def configure_trace_sink(sink: TraceSink | None) -> None:
    global _sink
    _sink = sink


def owner_id(identity: dict | None = None) -> str:
    state = request_identity.get() if identity is None else identity
    device = (state or {}).get("gateway_device_id")
    return f"device:{device}" if device else "local"


def active_trace() -> Trace | None:
    return _active.get()


class Trace:
    def __init__(self, scope: TraceScope, correlations: dict[str, str | None]) -> None:
        self.id = uuid.uuid4().hex
        self.scope = scope
        self.owner = owner_id()
        self.started = time.time()
        self.sequence = 0
        self.lock = threading.Lock()
        self.secrets: set[str] = set()
        self.sink = _sink
        self.policy = TracePolicy(mode="off")
        self.dropped = 0
        self.status = "completed"
        self.span_outcomes: dict[str, str] = {}
        if self.sink:
            with contextlib.suppress(Exception):
                self.policy = self.sink.policy(self.owner)
        if self.policy.full_until and self.started > self.policy.full_until:
            self.policy = self.policy.model_copy(update={"mode": "summary"})
        self.emit(
            "trace_started",
            {
                "schema": TRACE_SCHEMA,
                "scope": scope.model_dump(),
                "owner": self.owner,
                "origin": "server",
                "mode": self.policy.mode,
                "correlations": correlations,
                "started_at": self.started,
                "status": "running",
                "capture_status": "recording",
            },
        )

    def emit(self, event_type: str, data: dict, *, raw: bytes | None = None) -> None:
        if not self.sink or self.policy.mode == "off":
            return
        try:
            with self.lock:
                self.sequence += 1
                record = {
                    "event_id": uuid.uuid4().hex,
                    "trace_id": self.id,
                    "source_id": self.id,
                    "sequence": self.sequence,
                    "timestamp": time.time(),
                    "event_type": event_type,
                    "span_id": _parent.get(),
                    "data": data,
                }
                if not self.sink.submit(record, raw, tuple(self.secrets)):
                    self.dropped += 1
        except Exception:
            self.dropped += 1


class Span:
    def __init__(self, kind: str, label: str = "", **attributes) -> None:
        self.trace = active_trace()
        self.id = uuid.uuid4().hex
        self.parent = _parent.get()
        self.kind = kind
        self.label = label[:200]
        self.started = time.monotonic()
        self.finished = False
        if self.trace:
            self.trace.emit(
                "span_started",
                {
                    "span_id": self.id,
                    "parent_span_id": self.parent,
                    "kind": kind,
                    "label": self.label,
                    "status": "running",
                    **attributes,
                },
            )

    def finish(self, status: str = "completed", **metadata: Any) -> None:
        if self.finished:
            return
        self.finished = True
        if self.trace:
            if status == "completed":
                status = self.trace.span_outcomes.pop(self.id, status)
            if (self.kind == "turn" or self.parent is None) and status != "completed":
                self.trace.status = status
            self.trace.emit(
                "span_finished",
                {
                    "span_id": self.id,
                    "status": status,
                    "duration_ms": max(0, (time.monotonic() - self.started) * 1000),
                    **metadata,
                },
            )

    def __enter__(self) -> Span:
        self.token = _parent.set(self.id)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        status = (
            "completed"
            if exc_type is None
            else (
                "cancelled" if exc_type.__name__ in {"CancelledError", "GeneratorExit"} else "error"
            )
        )
        self.finish(status, error_type=exc_type.__name__ if exc_type else None)
        with contextlib.suppress(ValueError):
            _parent.reset(self.token)


@contextlib.contextmanager
def trace_scope(scope: TraceScope, **correlations: str | None):
    previous = active_trace()
    if previous is not None and previous.scope == scope:
        correlate(**correlations)
        yield previous
        return
    trace = Trace(scope, correlations)
    token = _active.set(trace)
    parent_token = _parent.set(None)
    if previous:
        trace.emit("correlation", {"parent_trace_id": previous.id})
    status = "completed"
    try:
        yield trace
    except BaseException as exc:
        status = (
            "cancelled" if type(exc).__name__ in {"CancelledError", "GeneratorExit"} else "error"
        )
        raise
    finally:
        trace.emit(
            "trace_finished",
            {
                "status": trace.status if status == "completed" else status,
                "finished_at": time.time(),
                "dropped_events": trace.dropped,
                "capture_status": "partial" if trace.dropped else "recorded",
            },
        )
        with contextlib.suppress(ValueError):
            _active.reset(token)
            _parent.reset(parent_token)


def correlate(**values: str | None) -> None:
    trace = active_trace()
    if trace:
        trace.emit("correlation", {key: str(value) for key, value in values.items() if value})


def record_payload(layer: str, value: Any, *, source: str = "application") -> None:
    trace = active_trace()
    if not trace:
        return
    # Serialization is injected with the sink; application modules do not import infrastructure.
    record = {
        "layer": layer,
        "capture_source": source,
        "media_type": "application/json",
        "completeness": "complete",
        "mode": trace.policy.mode,
    }
    if trace.policy.mode != "full":
        trace.emit(
            "payload",
            {**record, "completeness": "not_recorded", "missing_reason": "recording_not_enabled"},
        )
        return
    encoder = getattr(trace.sink, "encode", None)
    try:
        raw = encoder(value) if encoder else None
        trace.emit(
            "payload",
            {
                **record,
                "completeness": "complete" if raw is not None else "partial",
                "missing_reason": None if raw is not None else "size_or_encoding_limit",
            },
            raw=raw,
        )
    except Exception:
        trace.dropped += 1


def record_lazy(layer: str, factory) -> None:
    trace = active_trace()
    if trace is None:
        return
    try:
        record_payload(layer, factory() if trace.policy.mode == "full" else None)
    except Exception:
        trace.dropped += 1


def record_business_event(event: dict) -> None:
    trace = active_trace()
    if trace and event.get("type") in {"error", "cancelled", "superseded"}:
        trace.status = "error" if event["type"] == "error" else event["type"]
    record_payload("adapter_output", event)


def record_tool_outcome(result: dict) -> None:
    """Use the executor's explicit status; no interpretation of model text."""
    trace = active_trace()
    if trace and result.get("status") in {"error", "denied", "blocked", "failed", "conflict"}:
        trace.span_outcomes[_parent.get()] = "error"
