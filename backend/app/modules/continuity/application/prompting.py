"""Continuity prompt use cases."""
from __future__ import annotations

from typing import Protocol


class PromptRenderer(Protocol):
    def render(self, spec_id: str, **values: object) -> str: ...


class ContinuityPromptService:
    """Render cataloging prompts from the same compiled source."""

    def __init__(self, renderer: PromptRenderer) -> None:
        self._renderer = renderer

    def render_merged_cataloging(self) -> str:
        return self._renderer.render("continuity.cataloging.merged")

    def render_external_cataloging(self) -> str:
        return self._renderer.render("continuity.cataloging.external")


__all__ = ["ContinuityPromptService", "PromptRenderer"]
