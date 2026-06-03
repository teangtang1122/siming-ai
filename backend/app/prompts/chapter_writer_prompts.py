"""Chapter Writer prompt — assembles full writing rules for chapter body generation."""
from __future__ import annotations

import json as _json

from .anti_ai_prompts import build_anti_ai_system_prompt
from .chapter_prompts import CHAPTER_ENDING_HOOK_TYPES, CHAPTER_OPENING_HOOKS, LITERARY_TECHNIQUES
from .craft_prompts import build_craft_system_prompt
from .dialogue_prompts import build_dialogue_system_prompt
from .paragraph_hooks_prompts import build_paragraph_hooks_system_prompt


def build_chapter_writer_messages(
    *,
    style_context: str,
    outline_context: str,
    world_context: str,
    character_profiles: str,
    recent_summaries: str,
    plot_design: dict | None = None,
    roleplay_results: list[dict] | None = None,
    requirements: str = "",
) -> list[dict[str, str]]:
    """Backward-compatible wrapper — delegates to the pack system (quality mode).

    Returns [system_message, user_message] ready for LLMGateway.chat_completion().
    """
    from .packs.chapter_quality import PACK as CHAPTER_QUALITY_PACK
    from ..services.agent.prompt_builder import compose_chapter_writer_messages
    return compose_chapter_writer_messages(
        pack=CHAPTER_QUALITY_PACK,
        style_context=style_context,
        outline_context=outline_context,
        world_context=world_context,
        character_profiles=character_profiles,
        recent_summaries=recent_summaries,
        plot_design=plot_design,
        roleplay_results=roleplay_results,
        requirements=requirements,
    )
