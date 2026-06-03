"""Unified return contract for workspace tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Standardized return type for all workspace tools.

    Existing handlers that return plain dicts continue to work.
    New/updated handlers can use ToolResult for the expanded fields
    (refs, warnings, next_suggestions).
    """

    tool: str
    status: str  # "ok" | "skipped" | "error"
    detail: str
    data: Any = None
    refs: list[dict] | None = None
    warnings: list[str] | None = None
    next_suggestions: list[str] | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "tool": self.tool,
            "status": self.status,
            "detail": self.detail,
            "data": self.data,
        }
        if self.refs:
            d["refs"] = self.refs
        if self.warnings:
            d["warnings"] = self.warnings
        if self.next_suggestions:
            d["next_suggestions"] = self.next_suggestions
        return d
