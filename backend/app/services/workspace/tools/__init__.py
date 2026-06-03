"""Workspace tool handlers grouped by domain."""
from .chapter_writer import chapter_writer
from .chapters import create_chapter, delete_chapter, update_chapter
from .context_preview import preview_writing_context
from .character_writer import character_writer
from .characters import create_character, delete_character, update_character
from .outline import create_outline_node, delete_outline_node, update_outline_node
from .outline_writer import outline_writer
from .plot import design_plot
from .relationships import create_relationship, delete_relationship, update_relationship
from .analysis import detect_character_changes, detect_forbidden_patterns, detect_new_worldbuilding, detect_worldbuilding_conflicts, evaluate_chapter, suggest_conflicts
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
from .memory import forget, list_memories, recall, remember
from .web_search import web_search
from .rag_tools import search_context, preview_rag_context, explain_context_selection
from .worldbuilding import (
    create_worldbuilding_entry,
    delete_worldbuilding_entry,
    update_worldbuilding_entry,
)
from .worldbuilding_writer import worldbuilding_writer

__all__ = [
    "chapter_writer",
    "preview_writing_context",
    "character_writer",
    "outline_writer",
    "worldbuilding_writer",
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
    "design_plot",
    "detect_character_changes",
    "detect_new_worldbuilding",
    "detect_worldbuilding_conflicts",
    "detect_forbidden_patterns",
    "evaluate_chapter",
    "web_search",
    "remember",
    "recall",
    "forget",
    "list_memories",
]
