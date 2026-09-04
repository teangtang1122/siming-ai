"""Canonical JSON persistence contract for workspace assistant run steps."""
from __future__ import annotations

import hashlib
import json
from typing import Any

RUN_LOG_META_KEY = "_siming_run_log"
RUN_LOG_PAYLOAD_VERSION = 1
LEGACY_TRUNCATION_SUFFIX = "...[truncated]"
DIRECT_MCP_RETRY_BLOCK_REASON = (
    "Direct MCP 步骤不能脱离原 lease 重试；请发起新的作者消息。"
)

_UNRECOVERABLE_REQUEST_MESSAGE = (
    "该步骤的历史请求参数不完整，无法安全重试；请重新发起原任务。"
)


class UnrecoverableStepRequest(ValueError):
    """Raised when persisted tool arguments cannot be replayed exactly."""


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def serialize_step_request(data: Any) -> str:
    """Serialize replayable tool arguments without truncation.

    A retry must receive the exact argument object selected by the model.  It is
    therefore unsafe to shorten, summarize, or replace this payload.
    """

    if not isinstance(data, dict):
        raise ValueError("步骤请求参数必须是 JSON 对象")
    try:
        # Tool arguments originate from the model's JSON protocol.  Reject
        # non-JSON values instead of silently stringifying them, because such a
        # conversion would no longer be an exact replay of the original call.
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("步骤请求参数无法完整序列化，未执行该步骤") from exc


def serialize_step_result(data: Any) -> str:
    """Persist the complete JSON result as the execution audit source.

    Model-visible projection and conversation compaction happen only after
    this durable write. A large result must therefore use an artifact
    reference at the tool contract boundary, never an audit-log truncation.
    """

    try:
        text = _dumps(data)
    except (TypeError, ValueError, RecursionError):
        return _dumps(
            {
                RUN_LOG_META_KEY: {
                    "version": RUN_LOG_PAYLOAD_VERSION,
                    "kind": "unavailable_result",
                },
                "_unavailable": True,
                "reason": "serialization_failed",
                "value_type": type(data).__name__,
            }
        )
    return text


def _payload_kind(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    metadata = value.get(RUN_LOG_META_KEY)
    if not isinstance(metadata, dict):
        return None
    if metadata.get("version") != RUN_LOG_PAYLOAD_VERSION:
        return None
    kind = metadata.get("kind")
    return str(kind) if kind else None


def deserialize_step_request(raw: str | None) -> dict[str, Any]:
    """Load exact replay arguments or reject the step before tool execution."""

    if raw is None or raw == "":
        return {}
    if raw.endswith(LEGACY_TRUNCATION_SUFFIX):
        raise UnrecoverableStepRequest(_UNRECOVERABLE_REQUEST_MESSAGE)
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise UnrecoverableStepRequest(
            "该步骤的请求参数记录已损坏，无法安全重试；请重新发起原任务。"
        ) from exc
    if _payload_kind(value) == "unrecoverable_request":
        raise UnrecoverableStepRequest(_UNRECOVERABLE_REQUEST_MESSAGE)
    if not isinstance(value, dict):
        raise UnrecoverableStepRequest(
            "该步骤的请求参数不是 JSON 对象，无法安全重试；请重新发起原任务。"
        )
    return value


def step_request_retry_block_reason(raw: str | None) -> str | None:
    """Return a user-facing reason when a persisted request cannot be replayed."""

    try:
        deserialize_step_request(raw)
    except UnrecoverableStepRequest as exc:
        return str(exc)
    return None


def deserialize_step_value_for_display(raw: str | None) -> Any:
    """Decode a persisted value without sending malformed legacy JSON to clients."""

    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        preview = raw
        if preview.endswith(LEGACY_TRUNCATION_SUFFIX):
            preview = preview[: -len(LEGACY_TRUNCATION_SUFFIX)]
        return {
            RUN_LOG_META_KEY: {
                "version": RUN_LOG_PAYLOAD_VERSION,
                "kind": "corrupt_legacy_payload",
            },
            "_unavailable": True,
            "stored_chars": len(raw),
            "stored_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "preview": preview[:4096],
        }


__all__ = [
    "DIRECT_MCP_RETRY_BLOCK_REASON",
    "LEGACY_TRUNCATION_SUFFIX",
    "RUN_LOG_META_KEY",
    "RUN_LOG_PAYLOAD_VERSION",
    "UnrecoverableStepRequest",
    "deserialize_step_request",
    "deserialize_step_value_for_display",
    "serialize_step_request",
    "serialize_step_result",
    "step_request_retry_block_reason",
]
