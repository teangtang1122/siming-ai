"""Read-side deconstruction application port."""
from __future__ import annotations

from typing import Protocol


class DeconstructionReader(Protocol):
    def preview(self, project_id: str) -> dict: ...

    def reports(self, project_id: str, limit: int = 20) -> dict: ...


__all__ = ["DeconstructionReader"]
