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
    """Build messages for chapter body generation with full writing rules.

    Returns [system_message, user_message] ready for LLMGateway.chat_completion().
    """
    craft_rules = build_craft_system_prompt()
    dialogue_rules = build_dialogue_system_prompt()
    anti_ai_rules = build_anti_ai_system_prompt()
    hooks_rules = build_paragraph_hooks_system_prompt()

    system_prompt = (
        "你是一位资深小说写手，专精于将剧情设计和对白素材织成流畅、有感染力的章节正文。\n\n"
        "【任务】\n"
        "根据提供的剧情设计、角色对白素材和项目上下文，写出完整的章节正文。你不是在写大纲或摘要——你是直接交付可发布的正文。\n\n"
        "【写作原则】\n"
        "1. 剧情设计是你的骨架——其中指定的场景、冲突、情绪走向必须被遵守，但具体的措辞和描写由你决定。\n"
        "2. 角色扮演的对白是你的血肉——将对话自然地织入叙事中，用动作和细节连接对话段落。\n"
        "3. 叙事视角和文风严格遵循【风格设定】。\n"
        "4. 正文控制在 1800-2500 字。不长不短。\n"
        "5. 短句、动作描写、感官细节优先。不要写元评论、水词、抽象抒情。\n\n"
        "【章节结构】\n"
        "- 开头：用章首引子切入——悬念对白、中断动作、倒计时、或意象伏笔。禁止以背景交代或环境描写开头。\n"
        "- 中段：场景之间用蒙太奇切换，不需要过渡句。短句快切制造紧张，细节感官制造舒缓。每章至少 2 个紧张峰值。\n"
        "- 结尾：必须使用至少 1 种章末悬念钩子收束，禁止平淡过渡结尾。\n\n"
        "【输出格式】\n"
        "只输出章节正文本身。不要加任何前言、后记、解释或元评论。不要加章节标题（标题由系统自动添加）。\n"
        "不要使用 Markdown 格式。段落用空行分隔。\n\n"
        f"{craft_rules}\n\n"
        f"{dialogue_rules}\n\n"
        f"{anti_ai_rules}\n\n"
        f"{hooks_rules}\n\n"
        "【章首引子类型】\n"
        f"{CHAPTER_OPENING_HOOKS}\n\n"
        "【章末钩子类型】\n"
        f"{CHAPTER_ENDING_HOOK_TYPES}\n\n"
        "【文学技法】\n"
        f"{LITERARY_TECHNIQUES}\n\n"
        f"【风格设定】\n{style_context}"
    )

    # Build user message from context
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
    user_parts.append("\n请根据以上素材，写出完整的章节正文（1800-2500 字）。直接输出正文，不要加任何说明。")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
