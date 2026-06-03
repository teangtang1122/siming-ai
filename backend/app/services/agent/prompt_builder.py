"""Layered prompt composition engine for the AI writing assistant.

Provides pack-based prompt building, chapter writer message composition,
and helper utilities for the workspace assistant agentic loop.
"""
from __future__ import annotations

import json as _json
import logging
import os
from typing import Any

from ...prompts.packs import PromptPack

logger = logging.getLogger(__name__)


# ── Pack accessors ──────────────────────────────────────────────────────

def get_workspace_pack(mode: str) -> PromptPack:
    """Return the workspace prompt pack for the given mode."""
    from ...prompts.packs.workspace_fast import PACK as WORKSPACE_FAST_PACK
    from ...prompts.packs.workspace_quality import PACK as WORKSPACE_QUALITY_PACK

    if mode == "fast":
        return WORKSPACE_FAST_PACK
    return WORKSPACE_QUALITY_PACK


def get_chapter_pack(mode: str) -> PromptPack:
    """Return the chapter writer prompt pack for the given mode."""
    from ...prompts.packs.chapter_fast import PACK as CHAPTER_FAST_PACK
    from ...prompts.packs.chapter_quality import PACK as CHAPTER_QUALITY_PACK

    if mode == "fast":
        return CHAPTER_FAST_PACK
    return CHAPTER_QUALITY_PACK


# ── System prompt builder ───────────────────────────────────────────────

def build_tool_policy_section(pack: PromptPack) -> str:
    """Generate a tool policy section from pack metadata."""
    lines: list[str] = []
    if pack.available_tools:
        lines.append(f"允许使用的工具：{', '.join(pack.available_tools)}")
    if pack.unavailable_tools:
        lines.append(f"以下工具尚未实现，不可调用：{', '.join(pack.unavailable_tools)}")
    if pack.forbidden_behaviors:
        lines.append("禁止行为：")
        for fb in pack.forbidden_behaviors:
            lines.append(f"  - {fb}")
    if not lines:
        return ""
    return "【工具策略】\n" + "\n".join(lines)


def build_system_prompt(pack: PromptPack, *, skill_prompts: str = "", **kwargs: Any) -> str:
    """Build the full system prompt from a pack.

    Composes the pack's own ``build_system_prompt`` output with an optional
    skill prompt section and a tool policy section derived from pack metadata.
    Returns ONLY the system prompt string; user message construction is the
    caller's responsibility.
    """
    mode_prompt = pack.build_system_prompt(**kwargs)
    if skill_prompts:
        mode_prompt = f"{mode_prompt}\n\n{skill_prompts}"
    tool_policy = build_tool_policy_section(pack)
    if tool_policy:
        return f"{mode_prompt}\n\n{tool_policy}"
    return mode_prompt


# ── Chapter writer message composition ──────────────────────────────────

def compose_chapter_writer_messages(
    *,
    pack: PromptPack,
    style_context: str,
    outline_context: str,
    world_context: str,
    character_profiles: str,
    recent_summaries: str,
    plot_design: dict | None = None,
    roleplay_results: list[dict] | None = None,
    requirements: str = "",
) -> list[dict[str, str]]:
    """Compose chapter writer messages from a chapter pack.

    Returns ``[system_message, user_message]`` ready for
    ``LLMGateway.chat_completion()``.
    """
    system_prompt = pack.build_system_prompt(style_context=style_context)

    user_parts: list[str] = []
    if requirements:
        user_parts.append(f"【写作要求】\n{requirements}\n")
    user_parts.append(f"【大纲上下文】\n{outline_context}")
    if world_context and world_context != "无世界观设定。":
        user_parts.append(f"【世界观背景】\n{world_context}")
    if character_profiles:
        user_parts.append(f"【角色档案】\n{character_profiles}")
    if recent_summaries and recent_summaries != "暂无前文章节。":
        user_parts.append(f"【前文摘要】\n{recent_summaries}")
    if plot_design:
        user_parts.append(f"【剧情设计】\n{_json.dumps(plot_design, ensure_ascii=False)}")
    if roleplay_results:
        user_parts.append(f"【角色对白素材】\n{_json.dumps(roleplay_results, ensure_ascii=False)}")

    word_target = "1500-2000" if "fast" in pack.name else "1800-2500"
    user_parts.append(
        f"\n请根据以上素材，写出完整的章节正文（{word_target} 字）。直接输出正文，不要加任何说明。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


# ── Action helpers ──────────────────────────────────────────────────────

def inject_assistant_mode(action: dict, assistant_mode: str) -> dict:
    """Inject ``mode`` into chapter_writer action arguments.

    The router calls this at every ``execute_workspace_action`` call site
    so that the chapter_writer tool respects the user's fast/quality choice
    even when the LLM does not pass a ``mode`` argument.
    """
    tool = str(action.get("tool") or "").strip()
    if tool != "chapter_writer":
        return action
    args = action.get("arguments")
    if not isinstance(args, dict):
        args = {}
    if "mode" not in args:
        args = {**args, "mode": assistant_mode}
        action = {**action, "arguments": args}
    return action


# ── Debug logging ───────────────────────────────────────────────────────

def debug_log_prompt(system_prompt: str) -> None:
    """Log the system prompt when ``DEBUG_PROMPT_PACKS`` env var is set.

    Never logs in production — gated by the environment variable.
    """
    if not os.environ.get("DEBUG_PROMPT_PACKS"):
        return
    logger.debug("[PromptPack] system_prompt (%d chars):\n%s", len(system_prompt), system_prompt)
