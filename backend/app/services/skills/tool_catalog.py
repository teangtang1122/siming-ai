"""Tool-catalog port consumed by skill management."""
from __future__ import annotations

from collections.abc import Callable

ToolCatalogProvider = Callable[[], list[dict]]

_provider: ToolCatalogProvider | None = None


def configure_tool_catalog(provider: ToolCatalogProvider) -> None:
    global _provider
    _provider = provider


def get_tool_catalog() -> list[dict]:
    if _provider is None:
        raise RuntimeError("Workspace tool catalog is not configured.")
    return _provider()


__all__ = [
    "ToolCatalogProvider",
    "configure_tool_catalog",
    "get_tool_catalog",
]
