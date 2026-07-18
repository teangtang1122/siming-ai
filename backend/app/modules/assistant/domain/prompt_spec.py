"""Prompt specification value objects shared by every AI entry point."""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field


class PromptBudget(BaseModel):
    """Deterministic limits for the fixed prompt and injected context."""

    fixed_chars: int = Field(default=12_000, gt=0)
    context_chars: int = Field(default=48_000, gt=0)


class GoldenCase(BaseModel):
    """Cheap assertions that run without contacting a model."""

    name: str
    required_text: list[str] = Field(default_factory=list)
    forbidden_text: list[str] = Field(default_factory=list)


class PromptSpec(BaseModel):
    """Authoritative prompt metadata plus its Markdown body."""

    id: str
    version: str
    kind: Literal["prompt", "fragment"] = "prompt"
    scope: str = "shared"
    visibility: Literal["internal", "public", "both"] = "internal"
    inputs: list[str] = Field(default_factory=list)
    output_format: str = "text"
    output_schema: dict[str, Any] | None = None
    tool_policy: str = "none"
    tools: list[str] = Field(default_factory=list)
    fragments: list[str] = Field(default_factory=list)
    budget: PromptBudget = Field(default_factory=PromptBudget)
    golden_cases: list[GoldenCase] = Field(default_factory=list)
    body: str
    source_path: str = ""


class CompiledPrompt(BaseModel):
    """Validated prompt template ready for deterministic rendering."""

    spec_id: str
    version: str
    scope: str
    visibility: str
    inputs: list[str]
    output_format: str
    output_schema: dict[str, Any] | None = None
    tool_policy: str
    tools: list[str]
    budget: PromptBudget
    template: str
    source_paths: list[str]
    sha256: str

    @classmethod
    def from_template(
        cls,
        *,
        spec: PromptSpec,
        template: str,
        source_paths: list[str],
    ) -> CompiledPrompt:
        digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
        return cls(
            spec_id=spec.id,
            version=spec.version,
            scope=spec.scope,
            visibility=spec.visibility,
            inputs=list(spec.inputs),
            output_format=spec.output_format,
            output_schema=spec.output_schema,
            tool_policy=spec.tool_policy,
            tools=list(spec.tools),
            budget=spec.budget,
            template=template,
            source_paths=source_paths,
            sha256=digest,
        )

    def render(self, **values: Any) -> str:
        missing = [name for name in self.inputs if name not in values]
        if missing:
            raise ValueError(
                f"Prompt {self.spec_id} is missing inputs: {', '.join(missing)}"
            )
        rendered = self.template.format_map({name: str(values[name]) for name in self.inputs})
        if len(rendered) > self.budget.fixed_chars + self.budget.context_chars:
            raise ValueError(f"Prompt {self.spec_id} exceeds its total character budget")
        return rendered


__all__ = ["CompiledPrompt", "GoldenCase", "PromptBudget", "PromptSpec"]
