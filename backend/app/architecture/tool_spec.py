"""Typed workspace tool specification."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
ToolCallable = Callable[[InputT], OutputT | Awaitable[OutputT]]


class LegacyToolInput(BaseModel):
    """Permissive input used while a legacy ToolDef awaits typed migration."""

    model_config = ConfigDict(extra="allow")


class WorkspaceToolResult(BaseModel):
    """Shared result envelope for workspace tools."""

    model_config = ConfigDict(extra="allow")

    tool: str | None = None
    status: str
    detail: str = ""
    data: Any = None
    warnings: list[str] = PydanticField(default_factory=list)


@dataclass(frozen=True)
class ToolSpec(Generic[InputT, OutputT]):
    """Single source for runtime validation and exported tool schemas."""

    name: str
    description: str
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: ToolCallable[InputT, OutputT] | None = None
    version: str = "1.0.0"
    aliases: tuple[str, ...] = ()
    tool_type: str = "read"
    idempotent: bool = False
    requires_confirmation: bool = False
    permission_tags: frozenset[str] = field(default_factory=frozenset)
    risk_level: str = "safe"
    expose_to_internal_agent: bool = True
    expose_to_scheduler: bool = True
    expose_to_mcp: bool = True
    estimated_cost: str = "free"
    writes_project_data: bool = False
    mcp_permission_pack: str = ""
    input_schema_override: dict[str, Any] | None = None

    def validate_input(self, value: InputT | dict[str, Any]) -> InputT:
        if isinstance(value, self.input_model):
            return value
        return self.input_model.model_validate(value)

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema(),
            },
        }

    def parameters_schema(self) -> dict[str, Any]:
        if self.input_schema_override is not None:
            return deepcopy(self.input_schema_override)
        return self.input_model.model_json_schema()

    def mcp_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters_schema(),
        }

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()

    def frontend_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "aliases": list(self.aliases),
            "tool_type": self.tool_type,
            "permission_tags": sorted(self.permission_tags),
            "risk_level": self.risk_level,
            "writes_project_data": self.writes_project_data,
            "expose_to_internal_agent": self.expose_to_internal_agent,
            "expose_to_scheduler": self.expose_to_scheduler,
            "expose_to_mcp": self.expose_to_mcp,
            "mcp_permission_pack": self.mcp_permission_pack,
            "requires_confirmation": self.requires_confirmation,
            "estimated_cost": self.estimated_cost,
            "idempotent": self.idempotent,
        }


def project_typed_tool_spec(
    source: Any,
    *,
    input_model: type[BaseModel],
    version: str,
) -> ToolSpec:
    """Project structural legacy metadata around one typed input contract."""
    return ToolSpec(
        name=source.name,
        description=source.description,
        input_model=input_model,
        output_model=WorkspaceToolResult,
        version=version,
        tool_type=source.tool_type,
        idempotent=source.idempotent,
        requires_confirmation=source.requires_confirmation,
        permission_tags=frozenset(source.permission_tags),
        risk_level=source.risk_level,
        expose_to_internal_agent=source.expose_to_internal_agent,
        expose_to_scheduler=source.expose_to_scheduler,
        expose_to_mcp=source.expose_to_mcp,
        estimated_cost=source.estimated_cost,
        writes_project_data=source.writes_project_data,
        mcp_permission_pack=source.mcp_permission_pack,
    )


__all__ = [
    "LegacyToolInput",
    "ToolSpec",
    "WorkspaceToolResult",
    "project_typed_tool_spec",
]
