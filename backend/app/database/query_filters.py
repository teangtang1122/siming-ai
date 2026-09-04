"""Shared deterministic filters for authoritative database projections."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_

CURRENT_WORLDBUILDING_STATUS = "active"
WORLDBUILDING_STATUSES = frozenset({"active", "superseded", "archived", "draft"})


def current_worldbuilding_clause(status_column: Any) -> Any:
    """Include current entries and legacy rows whose status was never stored."""

    normalized = func.lower(func.trim(status_column))
    return or_(status_column.is_(None), normalized.in_(("", CURRENT_WORLDBUILDING_STATUS)))


def is_current_worldbuilding_status(value: Any) -> bool:
    return (
        str(value or CURRENT_WORLDBUILDING_STATUS).strip().lower()
        == CURRENT_WORLDBUILDING_STATUS
    )


def normalized_worldbuilding_status(value: Any) -> str:
    status = str(value or CURRENT_WORLDBUILDING_STATUS).strip().lower()
    if status not in WORLDBUILDING_STATUSES:
        raise ValueError(
            "世界观状态必须是 active、superseded、archived 或 draft"
        )
    return status


__all__ = [
    "CURRENT_WORLDBUILDING_STATUS",
    "WORLDBUILDING_STATUSES",
    "current_worldbuilding_clause",
    "is_current_worldbuilding_status",
    "normalized_worldbuilding_status",
]
