"""Chapter fast pack backed by the compiled PromptSpec catalog."""
from __future__ import annotations

from ...modules.assistant.infrastructure.runtime import render_prompt
from . import PromptPack


def _build_system(*, style_context: str, writing_directives: str = "") -> str:
    """Render the compact fast-mode chapter writing prompt."""
    return render_prompt(
        "assistant.chapter.fast",
        writing_directives=writing_directives.strip() or "遵守本轮项目上下文与作者要求。",
        style_context=style_context.strip() or "遵循项目已有文风。",
    )


PACK = PromptPack(
    name="chapter_fast",
    version="3.0.0",
    pack_type="chapter",
    description="Fast chapter writer with compact direct-writing rules",
    input_fields=[
        "style_context",
        "outline_context",
        "world_context",
        "character_profiles",
        "recent_summaries",
        "writing_directives",
    ],
    max_token_budget=6000,
    output_format="prose",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止添加前言、后记、解释或元评论",
        "禁止添加章节标题",
        "禁止使用 Markdown 格式",
        "快速模式不得降低角色、设定和时间线一致性要求",
        "写后必须通过 archive_chapter_after_write 提交归档候选",
    ],
    default_temperature=0.8,
    default_max_tokens=4000,
    context_budget={
        "style": 1500,
        "outline": 2000,
        "world": 1500,
        "characters": 1500,
        "summaries": 1000,
    },
    tool_policy="none",
    build_system_prompt=_build_system,
)
