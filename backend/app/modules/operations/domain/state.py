"""Lifecycle, health, and outcome rules for every long-running operation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

LIFECYCLE_STATUSES = {
    "draft",
    "queued",
    "running",
    "waiting_user",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
}
ACTIVE_STATUSES = {"queued", "running", "waiting_user", "paused"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
HEALTH_VALUES = {"active", "quiet", "suspected_stall", "stalled", "disconnected"}
OUTCOME_VALUES = {
    "completed_with_reply",
    "completed_with_tools",
    "partial_success",
    "empty_response",
    "skipped_preflight",
    "waiting_user",
    "blocked",
    "failed",
    "cancelled",
    "interrupted",
}
LEGACY_STATUS_MAP = {
    "created": "queued",
    "pending": "queued",
    "processing": "running",
    "in_progress": "running",
    "waiting_confirmation": "waiting_user",
    "awaiting_confirmation": "waiting_user",
    "error": "failed",
    "aborted": "interrupted",
}


def project_lifecycle_status(status: str | None) -> str:
    value = str(status or "running").strip().lower()
    projected = LEGACY_STATUS_MAP.get(value, value)
    return projected if projected in LIFECYCLE_STATUSES else "running"


def default_outcome(status: str, result: dict[str, Any] | None) -> str | None:
    payload = result or {}
    explicit = str(payload.get("outcome") or "").strip()
    if explicit in OUTCOME_VALUES:
        return explicit
    if status in {"waiting_user", "failed", "cancelled", "interrupted"}:
        return status
    if status != "completed":
        return None
    completed = payload.get("completed")
    incomplete = payload.get("incomplete")
    if isinstance(completed, list) and completed and isinstance(incomplete, list) and incomplete:
        return "partial_success"
    if str(payload.get("reply") or "").strip():
        return "completed_with_reply"
    if any(payload.get(key) for key in ("changes", "created", "updated", "tool_results")):
        return "completed_with_tools"
    return "empty_response"


def derive_health(
    *,
    status: str,
    health_status: str | None,
    heartbeat_at: datetime | None,
    last_activity_at: datetime | None,
    last_output_at: datetime | None,
    created_at: datetime | None,
    updated_at: datetime | None,
    now: datetime,
) -> str:
    if project_lifecycle_status(status) not in ACTIVE_STATUSES:
        return health_status or "active"
    heartbeat = heartbeat_at or updated_at or created_at
    if heartbeat and now - heartbeat > timedelta(seconds=60):
        return "disconnected"
    activity = last_activity_at or created_at
    if activity and now - activity > timedelta(minutes=30):
        return "suspected_stall"
    output = last_output_at or created_at
    if output and now - output > timedelta(minutes=10):
        return "quiet"
    return health_status or "active"
