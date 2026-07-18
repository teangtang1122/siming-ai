"""Compose domain-owned ToolSpecs without importing the legacy registry."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ....architecture.tool_spec import ToolSpec
from ...continuity.interfaces.tool_specs import build_continuity_tool_specs
from ...creation.interfaces.tool_specs import build_creation_tool_specs


def build_domain_tool_specs(definitions: Mapping[str, Any]) -> list[ToolSpec]:
    return [
        *build_creation_tool_specs(definitions),
        *build_continuity_tool_specs(definitions),
    ]


__all__ = ["build_domain_tool_specs"]
