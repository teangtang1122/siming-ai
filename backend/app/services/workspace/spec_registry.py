"""ToolSpec projection support kept outside the legacy registry module."""
from __future__ import annotations

from typing import Any

from ...architecture.tool_spec import LegacyToolInput, ToolSpec, WorkspaceToolResult


class ToolSpecRegistryMixin:
    """Build typed projections while legacy ToolDefs are migrated by domain."""

    _tools: dict[str, Any]
    _specs: dict[str, ToolSpec]
    _aliases: dict[str, str]

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(self._aliases.get(name, name))

    def all_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def bind_spec(self, spec: ToolSpec) -> None:
        if spec.name not in self._tools:
            raise KeyError(f"Cannot bind ToolSpec for unknown tool: {spec.name}")
        self._specs[spec.name] = spec
        for alias in spec.aliases:
            if alias in self._tools or alias in self._aliases:
                raise ValueError(f"Duplicate workspace tool alias: {alias}")
            self._aliases[alias] = spec.name

    def bind_specs(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            self.bind_spec(spec)

    def rebuild_legacy_specs(self) -> None:
        self._specs = {
            name: self._legacy_spec(tool_def)
            for name, tool_def in self._tools.items()
        }
        self._aliases.clear()

    @staticmethod
    def _legacy_spec(tool_def: Any) -> ToolSpec:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": tool_def.input_schema,
        }
        if tool_def.required:
            schema["required"] = list(tool_def.required)
        return ToolSpec(
            name=tool_def.name,
            description=tool_def.description,
            input_model=LegacyToolInput,
            output_model=WorkspaceToolResult,
            tool_type=tool_def.tool_type,
            idempotent=tool_def.idempotent,
            requires_confirmation=tool_def.requires_confirmation,
            permission_tags=frozenset(tool_def.permission_tags),
            risk_level=tool_def.risk_level,
            expose_to_internal_agent=tool_def.expose_to_internal_agent,
            expose_to_scheduler=tool_def.expose_to_scheduler,
            expose_to_mcp=tool_def.expose_to_mcp,
            estimated_cost=tool_def.estimated_cost,
            writes_project_data=tool_def.writes_project_data,
            mcp_permission_pack=tool_def.mcp_permission_pack,
            input_schema_override=schema,
        )


__all__ = ["ToolSpecRegistryMixin"]
