"""Workspace tool handlers grouped by domain."""
from .chapters import create_chapter
from .characters import create_character, update_character
from .outline import create_outline_node, update_outline_node
from .relationships import create_relationship
from .worldbuilding import create_worldbuilding_entry, update_worldbuilding_entry

__all__ = [
    "create_chapter",
    "create_character",
    "update_character",
    "create_outline_node",
    "update_outline_node",
    "create_relationship",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
]

