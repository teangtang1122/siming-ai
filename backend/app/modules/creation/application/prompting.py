"""New-novel prompt use cases."""
from __future__ import annotations

from typing import Protocol


class PromptRenderer(Protocol):
    def render(self, spec_id: str, **values: object) -> str: ...


class NovelCreationPromptService:
    """Render creation prompts without depending on storage or model adapters."""

    def __init__(self, renderer: PromptRenderer) -> None:
        self._renderer = renderer

    def render_stage(self, *, task_kind: str, task_rules: str) -> str:
        return self._renderer.render(
            "creation.novel.stage",
            task_kind=task_kind,
            task_rules=task_rules,
        )


__all__ = ["NovelCreationPromptService", "PromptRenderer"]
