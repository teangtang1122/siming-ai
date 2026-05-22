"""Workspace tool handlers grouped by domain."""
from .chapters import create_chapter, delete_chapter, update_chapter
from .characters import create_character, delete_character, update_character
from .outline import create_outline_node, delete_outline_node, update_outline_node
from .relationships import create_relationship, delete_relationship, update_relationship
from .analysis import detect_character_changes, detect_worldbuilding_conflicts, suggest_conflicts
from .roleplay import dialogue_battle, roleplay_character
from .text_operations import continue_text, expand_text, rewrite_text
from .search import (
    list_characters,
    list_chapters,
    list_worldbuilding,
    search_chapters,
    search_characters,
    search_outline,
    search_outline_tree,
    search_relationships,
    search_worldbuilding,
)
from .worldbuilding import (
    create_worldbuilding_entry,
    delete_worldbuilding_entry,
    update_worldbuilding_entry,
)

__all__ = [
    "create_chapter",
    "update_chapter",
    "delete_chapter",
    "create_character",
    "update_character",
    "delete_character",
    "create_outline_node",
    "update_outline_node",
    "delete_outline_node",
    "create_relationship",
    "update_relationship",
    "delete_relationship",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
    "delete_worldbuilding_entry",
    "list_characters",
    "list_chapters",
    "list_worldbuilding",
    "search_characters",
    "search_chapters",
    "search_outline",
    "search_outline_tree",
    "search_worldbuilding",
    "search_relationships",
    "roleplay_character",
    "dialogue_battle",
    "rewrite_text",
    "expand_text",
    "continue_text",
    "suggest_conflicts",
    "detect_character_changes",
    "detect_worldbuilding_conflicts",
]
