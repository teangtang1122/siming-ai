"""Compatibility helpers for creation artifact patch operations."""
from __future__ import annotations

from typing import Any


def normalize_patch_operation(change: dict[str, Any]) -> tuple[str, str]:
    path = str(change.get("path") or "").strip()
    action = str(change.get("action") or "").strip()
    standard_op = str(change.get("op") or "").strip()
    if not action and standard_op == "add":
        if path.endswith("/-"):
            return path[:-2] or "/", "append"
        return path, "set"
    if not action and standard_op in {"replace", "remove"}:
        action = standard_op
    return path, action
