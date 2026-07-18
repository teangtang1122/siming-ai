"""Chapter quality pack backed by the compiled PromptSpec catalog."""
from __future__ import annotations

from ...modules.assistant.infrastructure.runtime import render_prompt
from . import PromptPack


def _build_system(*, style_context: str, writing_directives: str = "") -> str:
    return render_prompt(
        "assistant.chapter.quality",
        writing_directives=writing_directives.strip() or "遵守本轮项目上下文与作者要求。",
        style_context=style_context.strip() or "遵循项目既有文风。",
    )


PACK = PromptPack(
    name="chapter_quality",
    version="3.0.0",
    pack_type="chapter",
    description="Quality chapter writer — full craft rules, dialogue, hooks, literary techniques",
    input_fields=[
        "style_context", "outline_context", "world_context",
        "character_profiles", "recent_summaries",
        "plot_design", "roleplay_results", "requirements", "writing_directives",
    ],
    max_token_budget=12000,
    output_format="prose",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止添加前言、后记、解释或元评论",
        "禁止添加章节标题",
        "禁止使用 Markdown 格式",
        "正文必须控制在 1800-2500 字",
    ],
    default_temperature=0.8,
    default_max_tokens=6000,
    context_budget={"style": 2000, "outline": 3000, "world": 2000, "characters": 2000, "summaries": 1500},
    tool_policy="none",
    build_system_prompt=_build_system,
)
