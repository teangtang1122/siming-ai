"""Prompt Pack system — composable, declarative prompt segments."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class PromptPack:
    """Declarative metadata for a composable prompt segment.

    Each pack declares what it needs, what it produces, and what it forbids.
    The ``build_system_prompt`` callable returns the actual prompt text at runtime.
    """

    name: str
    version: str
    pack_type: str  # "workspace" | "chapter" | "cataloging" | "research" | "memory"
    description: str
    input_fields: list[str]
    max_token_budget: int
    output_format: str  # "prose" | "json" | "jsonl" | "text_reply"
    output_schema: dict | None
    available_tools: list[str]
    unavailable_tools: list[str] = field(default_factory=list)
    forbidden_behaviors: list[str] = field(default_factory=list)
    default_temperature: float | None = None
    default_max_tokens: int | None = None
    context_budget: dict = field(default_factory=dict)
    tool_policy: str = "none"  # "full" | "search_only" | "none" | "custom"
    build_system_prompt: Callable[..., str] = field(default=lambda: lambda **kw: "")
