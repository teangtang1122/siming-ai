"""Transport-neutral workspace tool metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolDef:
    """Stable tool definition shared by internal, MCP, CLI, and GUI adapters."""

    name: str
    description: str
    input_schema: dict[str, Any]
    required: list[str] = field(default_factory=list)
    tool_type: str = "read"
    idempotent: bool = False
    requires_confirmation: bool = False
    estimated_cost: str = "free"
    handler: ToolHandler | None = None
    handler_name: str = ""
    permission_tags: set[str] = field(default_factory=set)
    risk_level: str = "safe"
    writes_project_data: bool = False
    expose_to_internal_agent: bool = True
    expose_to_scheduler: bool = True
    expose_to_mcp: bool = True
    mcp_permission_pack: str = ""

    def bind(self, resolve_handler: Callable[[str], ToolHandler]) -> ToolDef:
        """Return a runtime definition with its legacy handler attached."""

        if self.handler is not None or not self.handler_name:
            return self
        return replace(self, handler=resolve_handler(self.handler_name))


__all__ = ["ToolDef", "ToolHandler"]
