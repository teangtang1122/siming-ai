"""Backward-compatible imports for workspace assistant actions."""
from .workspace import (
    WORLD_DIMENSIONS,
    WorkspaceActionDependencies,
    _character_ids_from_names,
    _character_payload,
    _find_character_by_name_or_id,
    _find_outline_by_title_or_id,
    _find_worldbuilding_by_title_or_id,
    _normalize_outline_lookup,
    _next_outline_sort_order,
    _next_worldbuilding_sort_order,
    _outline_node_payload,
    _replace_outline_links_by_names,
    _worldbuilding_payload,
    execute_workspace_action,
)

__all__ = [
    "WorkspaceActionDependencies",
    "WORLD_DIMENSIONS",
    "_character_payload",
    "_outline_node_payload",
    "_worldbuilding_payload",
    "_find_worldbuilding_by_title_or_id",
    "_find_character_by_name_or_id",
    "_normalize_outline_lookup",
    "_find_outline_by_title_or_id",
    "_character_ids_from_names",
    "_replace_outline_links_by_names",
    "_next_outline_sort_order",
    "_next_worldbuilding_sort_order",
    "execute_workspace_action",
]

