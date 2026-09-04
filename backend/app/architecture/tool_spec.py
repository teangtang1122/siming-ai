"""Typed workspace tool specification."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Generic, TypeVar

import fastjsonschema
from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

from .tool_result_policy import (
    DEFAULT_MODEL_RESULT_CONTRACT,
    ModelResultContract,
)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
ToolCallable = Callable[[InputT], OutputT | Awaitable[OutputT]]


class ToolInputSchemaValidationError(ValueError):
    """Model-visible JSON Schema rejected a tool argument object."""

    def __init__(self, *, path: tuple[str | int, ...], rule: str, expected: Any) -> None:
        self.path = path
        self.rule = rule
        self.expected = expected
        super().__init__(self.public_detail)

    @property
    def public_detail(self) -> str:
        location = "$"
        for part in self.path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        if self.rule == "required" and isinstance(self.expected, list):
            fields = "、".join(str(item) for item in self.expected)
            return f"{location} 缺少必填参数：{fields}"
        if self.rule == "type":
            return f"{location} 的类型必须是 {self.expected}"
        if self.rule == "enum" and isinstance(self.expected, list):
            choices = "、".join(str(item) for item in self.expected)
            return f"{location} 必须是以下值之一：{choices}"
        if self.rule in {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}:
            return f"{location} 未满足 {self.rule}={self.expected}"
        return f"{location} 未通过 {self.rule or 'schema'} 校验"


@lru_cache(maxsize=256)
def _compiled_input_schema(serialized_schema: str) -> Callable[[Any], Any]:
    schema = json.loads(serialized_schema)
    return fastjsonschema.compile(schema, use_default=False)


def _validate_exported_schema(schema: dict[str, Any], value: Any) -> None:
    serialized = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        _compiled_input_schema(serialized)(value)
    except fastjsonschema.JsonSchemaValueException as exc:
        raw_path = tuple(getattr(exc, "path", ()) or ())
        path = raw_path[1:] if raw_path and raw_path[0] == "data" else raw_path
        rule = str(getattr(exc, "rule", "") or "")
        expected = getattr(exc, "rule_definition", None)
        if rule == "required" and isinstance(expected, list) and isinstance(exc.value, dict):
            expected = [field for field in expected if field not in exc.value]
        raise ToolInputSchemaValidationError(
            path=path,
            rule=rule,
            expected=expected,
        ) from exc


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
    ends_agent_turn: bool = False
    mcp_permission_pack: str = ""
    direct_mcp_project_scoped: bool = False
    direct_mcp_transactional: bool = False
    input_schema_override: dict[str, Any] | None = None
    model_result_contract: ModelResultContract = DEFAULT_MODEL_RESULT_CONTRACT

    def validate_input(self, value: InputT | dict[str, Any]) -> InputT:
        validated = (
            value
            if isinstance(value, self.input_model)
            else self.input_model.model_validate(value)
        )
        raw_value = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        _validate_exported_schema(self.parameters_schema(), raw_value)
        return validated

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
            "ends_agent_turn": self.ends_agent_turn,
            "expose_to_internal_agent": self.expose_to_internal_agent,
            "expose_to_scheduler": self.expose_to_scheduler,
            "expose_to_mcp": self.expose_to_mcp,
            "mcp_permission_pack": self.mcp_permission_pack,
            "direct_mcp_project_scoped": self.direct_mcp_project_scoped,
            "direct_mcp_transactional": self.direct_mcp_transactional,
            "requires_confirmation": self.requires_confirmation,
            "estimated_cost": self.estimated_cost,
            "idempotent": self.idempotent,
            "model_result_policy": self.model_result_contract.policy.value,
            "model_result_max_json_bytes": self.model_result_contract.max_json_bytes,
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
        ends_agent_turn=source.ends_agent_turn,
        mcp_permission_pack=source.mcp_permission_pack,
        direct_mcp_project_scoped=source.direct_mcp_project_scoped,
        direct_mcp_transactional=source.direct_mcp_transactional,
        model_result_contract=source.model_result_contract,
    )


__all__ = [
    "LegacyToolInput",
    "ToolInputSchemaValidationError",
    "ToolSpec",
    "WorkspaceToolResult",
    "project_typed_tool_spec",
]
