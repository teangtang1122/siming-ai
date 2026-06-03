"""Chapter Fast pack — compact writing rules for faster generation."""
from __future__ import annotations

from ..anti_ai_prompts import FORBIDDEN_SENTENCE_TEMPLATES, TIER1_BANNED_WORDS, TIER2_THRESHOLD_WORDS
from ..chapter_prompts import CHAPTER_ENDING_HOOK_TYPES, CHAPTER_OPENING_HOOKS
from ..craft_prompts import BODY_EMOTION_REPLACEMENT, SCENE_WEAVING_RULE
from ..dialogue_prompts import DIALOGUE_CORE_RULES
from . import PromptPack


def _build_compact_anti_ai() -> str:
    """Compact anti-AI rules: banned words + forbidden templates + rhetoric limits."""
    tier1_lines: list[str] = []
    for category, words in TIER1_BANNED_WORDS.items():
        tier1_lines.append(f"  {category}：{'、'.join(words)}")
    tier1_text = "\n".join(tier1_lines)

    template_lines: list[str] = []
    for name, example in FORBIDDEN_SENTENCE_TEMPLATES[:8]:
        template_lines.append(f"  - {name}：如「{example}」")
    template_text = "\n".join(template_lines)

    return (
        "【禁用词与句式 — 必须避免】\n\n"
        "一级禁用词（出现即替换）：\n"
        f"{tier1_text}\n\n"
        "二级阈值词（不可连续使用）：\n"
        f"  {'、'.join(TIER2_THRESHOLD_WORDS)}\n\n"
        "禁用句式模板：\n"
        f"{template_text}\n\n"
        "修辞限制：\n"
        "- 每千字最多 1 个明喻（像/如同/仿佛）\n"
        "- 禁止连续两段使用比喻\n"
        "- 禁止排比句超过 2 个分句\n"
    )


def _build_compact_dialogue() -> str:
    """Compact dialogue rules: core rules + voice differentiation."""
    return (
        f"{DIALOGUE_CORE_RULES}\n\n"
        "【角色声音区分】\n"
        "每个角色的说话方式必须可辨认：\n"
        "- 用词习惯（文雅/粗俗/简洁/啰嗦）\n"
        "- 句式长短（短句硬汉/长句学者）\n"
        "- 口头禅或语气词（适度使用，每千字不超过2次）\n"
        "- 对话中不要所有角色说话方式一样\n"
    )


def _build_system(*, style_context: str) -> str:
    """Build a compact chapter writer system prompt for fast mode.

    Includes key quality rules from each module but skips deep-dive sections.
    """
    anti_ai_compact = _build_compact_anti_ai()
    dialogue_compact = _build_compact_dialogue()

    return (
        "你是一位小说写手，专精于将剧情设计和对白素材织成流畅的章节正文。\n\n"
        "【任务】\n"
        "根据提供的剧情设计和项目上下文，写出完整的章节正文。直接交付可发布的正文。\n\n"
        "【写作原则】\n"
        "1. 剧情设计是你的骨架——场景、冲突、情绪走向必须被遵守。\n"
        "2. 叙事视角和文风严格遵循【风格设定】。\n"
        "3. 正文控制在 1500-2000 字。\n"
        "4. 短句、动作描写、感官细节优先。不要写元评论、水词、抽象抒情。\n\n"
        "【章节结构】\n"
        "- 开头：用章首引子切入。禁止以背景交代或环境描写开头。\n"
        "- 中段：短句快切制造紧张，细节感官制造舒缓。每章至少 1 个紧张峰值。\n"
        "- 结尾：必须使用章末悬念钩子收束，禁止平淡过渡结尾。\n\n"
        "【输出格式】\n"
        "只输出章节正文本身。不要加任何前言、后记、解释或元评论。不要加章节标题。\n"
        "不要使用 Markdown 格式。段落用空行分隔。\n\n"
        f"{BODY_EMOTION_REPLACEMENT}\n\n"
        f"{SCENE_WEAVING_RULE}\n\n"
        f"{dialogue_compact}\n\n"
        f"{anti_ai_compact}\n\n"
        "【章首引子类型】\n"
        f"{CHAPTER_OPENING_HOOKS}\n\n"
        "【章末钩子类型】\n"
        f"{CHAPTER_ENDING_HOOK_TYPES}\n\n"
        f"【风格设定】\n{style_context}"
    )


PACK = PromptPack(
    name="chapter_fast",
    version="1.0",
    pack_type="chapter",
    description="Fast chapter writer — compact craft/dialogue/anti-AI rules, shorter output",
    input_fields=[
        "style_context", "outline_context", "world_context",
        "character_profiles", "recent_summaries",
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
        "正文必须控制在 1500-2000 字",
    ],
    default_temperature=0.8,
    default_max_tokens=4000,
    context_budget={"style": 1500, "outline": 2000, "world": 1500, "characters": 1500, "summaries": 1000},
    tool_policy="none",
    build_system_prompt=_build_system,
)
