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
    """Return the workspace prompt pack.

    Moshu keeps the UI-facing ``mode`` flag for workflow hints, but the
    controlling agent prompt is intentionally unified on the highest-quality
    pack. This prevents the same user request from behaving differently when
    it enters through the web UI, Plan Agent, MCP, or a local CLI model.
    """
    from ...prompts.packs.workspace_quality import PACK as WORKSPACE_QUALITY_PACK

    return WORKSPACE_QUALITY_PACK


def get_chapter_pack(mode: str) -> PromptPack:
    """Return the chapter writer prompt pack.

    Chapter prose generation always uses the quality pack. A requested fast
    mode may still reduce orchestration elsewhere, but it must not downgrade
    the writing rules used to produce the chapter body.
    """
    from ...prompts.packs.chapter_quality import PACK as CHAPTER_QUALITY_PACK

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


def build_system_prompt(
    pack: PromptPack,
    *,
    skill_prompts: str = "",
    db: Any = None,
    public_pack_scope: str = "chapter_writing",
    public_pack_mode: str = "quality",
    **kwargs: Any,
) -> str:
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
        mode_prompt = f"{mode_prompt}\n\n{tool_policy}"

    # Inject public prompt pack section if db is available
    if db is not None:
        mode_prompt = inject_public_prompt_pack_section(mode_prompt, db, public_pack_scope, public_pack_mode)

    return mode_prompt


def inject_public_prompt_pack_section(
    system_prompt: str,
    db: Any,
    scope: str,
    mode: str = "quality",
) -> str:
    """Append a public prompt pack summary to the system prompt.

    This ensures the internal assistant and external agents see the same
    writing methodology. The public pack summary is appended as a reference
    section, not a replacement for the internal prompt.
    """
    try:
        from app.database.models import PublicPromptPack
        from app.services.prompt_packs.seed import ensure_builtin_packs
        from sqlalchemy.orm import Session as OrmSession

        if isinstance(db, OrmSession):
            ensure_builtin_packs(db)

        # Map scope+mode to pack_id
        scope_mode_map = {
            ("chapter_writing", "quality"): "chapter_writing_quality",
            ("chapter_writing", "fast"): "chapter_writing_quality",
            ("chapter_review", "quality"): "chapter_review_quality",
            ("new_project", ""): "new_project_setup",
            ("character_design", ""): "character_design",
            ("worldbuilding", ""): "worldbuilding_design",
            ("outline_planning", ""): "outline_planning",
            ("anti_ai_review", ""): "anti_ai_review",
        }
        pack_id = scope_mode_map.get((scope, mode), scope_mode_map.get((scope, ""), ""))
        if not pack_id:
            return system_prompt

        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()

        if not pack:
            return system_prompt

        # Append public pack reference section
        sections = [system_prompt]
        sections.append(f"\n\n【公开写作方法参考 — {pack.title} v{pack.version}】")
        if pack.summary:
            sections.append(f"方法概述：{pack.summary}")
        if pack.quality_rubric_json:
            rubric = pack.quality_rubric_json
            dims = rubric.get("dimensions", [])
            if dims:
                dim_names = ", ".join(d["name"] for d in dims[:5])
                sections.append(f"质量维度：{dim_names}")
        if pack.forbidden_patterns_json:
            patterns = pack.forbidden_patterns_json[:5]
            sections.append(f"禁用句式示例：{', '.join(patterns)}")

        return "".join(sections)
    except Exception:
        # If public pack loading fails, return original prompt unchanged
        return system_prompt


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

    word_target = "1800-2500"
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
