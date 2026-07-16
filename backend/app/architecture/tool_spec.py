"""Typed workspace tool specification."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
ToolCallable = Callable[[InputT], OutputT | Awaitable[OutputT]]


@dataclass(frozen=True)
class ToolSpec(Generic[InputT, OutputT]):
    """Single source for runtime validation and exported tool schemas."""

    name: str
    description: str
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: ToolCallable[InputT, OutputT] | None = None
    tool_type: str = "read"
    idempotent: bool = False
    requires_confirmation: bool = False
    permission_tags: frozenset[str] = field(default_factory=frozenset)
    risk_level: str = "safe"
    expose_to_internal_agent: bool = True
    expose_to_scheduler: bool = True
    expose_to_mcp: bool = True

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
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()
