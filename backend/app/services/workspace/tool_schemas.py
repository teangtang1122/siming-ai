"""Authorized schema projection for the model-selected workspace categories."""
from __future__ import annotations

from collections.abc import Iterable

from .registry import registry


def select_workspace_tool_names(categories: Iterable[str] | None = None) -> list[str]:
    """Return one model-visible capability catalog for the workspace Agent.

    Natural-language intent and target selection belong to the model. This
    function applies only deterministic server authorization and therefore
    only includes project capabilities and never branches on user wording or UI selection.
    """
    return sorted({
        tool.name
        for tool in registry.list_for_internal_agent(categories=categories)
        if tool.risk_level != "destructive"
    })


def build_workspace_tool_schemas(tool_names: Iterable[str]) -> list[dict]:
    wanted = set(tool_names)
    return [
        schema for schema in registry.get_schemas()
        if schema.get("function", {}).get("name") in wanted
    ]
