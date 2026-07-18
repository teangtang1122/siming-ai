"""Public prompt-pack query port."""
from __future__ import annotations

from typing import Protocol


class PromptPackCatalog(Protocol):
    def list_for_project(self, project_id: str) -> dict: ...


__all__ = ["PromptPackCatalog"]
