"""Single source of truth for prompt content.

This module provides functions to extract prompt content from the canonical
source files. Both internal assistant and external agent prompt packs read
from here — edit these files to change behavior everywhere.
"""
from __future__ import annotations


def get_forbidden_patterns() -> list[str]:
    """Get the complete forbidden patterns list from anti_ai_prompts.py."""
    from .anti_ai_prompts import (
        TIER1_BANNED_WORDS,
        TIER2_THRESHOLD_WORDS,
        TIER3_SENTENCE_PATTERNS,
        CHAPTER_END_BAN_PATTERNS,
    )

    patterns: list[str] = []
    # Tier 1 words
    for category_words in TIER1_BANNED_WORDS.values():
        patterns.extend(category_words)
    # Tier 2 threshold words
    patterns.extend(TIER2_THRESHOLD_WORDS)
    # Tier 3 sentence-level structural patterns
    patterns.extend(TIER3_SENTENCE_PATTERNS)
    # Chapter end patterns
    patterns.extend(CHAPTER_END_BAN_PATTERNS)
    return list(dict.fromkeys(patterns))  # dedupe, preserve order


def get_quality_rubric() -> dict:
    """Get the quality rubric for chapter evaluation."""
    return {
        "dimensions": [
            {"name": "opening_hook", "description": "开头吸引力：第一段是否能抓住读者", "max_score": 10},
            {"name": "plot_progression", "description": "情节推进：剧情是否有实质进展", "max_score": 10},
            {"name": "character_portrayal", "description": "角色塑造：角色是否立体、有记忆点", "max_score": 10},
            {"name": "dialogue_quality", "description": "对话质量：对话是否自然、有信息量", "max_score": 10},
            {"name": "suspense", "description": "悬念设置：是否有足够的钩子", "max_score": 10},
            {"name": "pacing", "description": "节奏控制：快慢是否得当", "max_score": 10},
            {"name": "show_dont_tell", "description": "展示性描写：是否用展示而非叙述", "max_score": 10},
            {"name": "language_quality", "description": "语言质量：文笔是否流畅", "max_score": 10},
        ],
        "passing_score": 60,
        "max_score": 80,
    }


def get_chapter_writing_rules() -> str:
    """Get the core chapter writing rules as a single text block."""
    return (
        "【正文要求】1800-2500字。开头要吸引人，章末要留钩子。展示而非叙述，短句优先。\n\n"
        "【剧情设计】写作前先设计：场景、冲突、情绪曲线、转折点、结尾钩子。\n"
        "【角色对话】每个角色说话要符合性格，对话要有信息量，推动剧情或揭示性格。"
    )


def get_time_tracking_rules() -> str:
    """Get time tracking rules for cataloging."""
    return (
        "【时间追踪规则】\n\n"
        "一、判断时间类型\n"
        "1. 主时间线推进：叙述视角描述的时间流逝（如\"三个月后\"、\"入冬以来\"），影响所有角色。\n"
        "2. 个体时间异常：仅某个角色经历的时间（幻境修炼、穿越到其他时间线、被封印），不影响其他角色。\n"
        "3. 判断依据：\n"
        "   - 如果叙述说\"外面过了三天，但她在幻境里度过了三年\" → 外面三天是主时间线，幻境三年是个体时间\n"
        "   - 如果叙述说\"三年后，她终于从幻境中醒来\" → 主时间线过了三年，她的经历年龄也多了三年\n"
        "   - 如果叙述说\"她穿越到了十年前\" → 这是时间跳跃，不是主时间线推进\n\n"
        "二、age 字段写法\n"
        "age 是描述性文本，不是精确数字。根据上下文合理推断：\n"
        "- 普通角色：\"约16岁\"、\"3岁\"、\"成年\"、\"老年\"\n"
        "- 有时间异常的角色：\"外表约16岁，实际经历约200年\"、\"3岁，但在幻境中经历了约100年\"\n"
        "- 不老/永生角色：\"外表16岁，实际200岁\"、\"外表约20岁，实际年龄不详\"\n"
        "- 年龄不明确时：\"青年\"、\"中年\"、\"老年\"、\"年龄不详\"\n\n"
        "三、主时间线推进时的处理\n"
        "1. 如果本章明确描述了时间推进（如\"一个月后\"），更新所有出场角色的 age。\n"
        "2. 不需要精确计算生日——用描述性文本即可（如\"现在约17岁了\"）。\n"
        "3. 如果某个角色本章没有出场，不要猜测性地更新他的 age。\n"
        "4. 如果时间推进很短（几天内），不需要更新 age。\n\n"
        "四、个体时间异常的处理\n"
        "1. 只更新该角色的 age，不更新其他角色。\n"
        "2. 在 mental_state 中注明时间感知差异（如\"刚从三年幻境中醒来，对外界的时间流逝感到困惑\"）。\n"
        "3. 输出 character_timeline 记录时间异常事件。\n\n"
        "五、不要做的事\n"
        "- 不要试图精确计算每个角色的生日和年龄差\n"
        "- 不要因为主时间线推进了几天就更新所有角色的 age\n"
        "- 不要猜测没有明确描述的时间流逝\n"
        "- 不要把个体时间异常当成主时间线推进"
    )


def get_character_change_detection_prompt() -> str:
    """Character change detection prompt — same as internal detect_character_changes tool."""
    from .analysis_prompts import CHARACTER_CHANGE_SYSTEM
    return CHARACTER_CHANGE_SYSTEM


def get_new_worldbuilding_detection_prompt() -> str:
    """New worldbuilding detection prompt — same as internal detect_new_worldbuilding tool."""
    from .analysis_prompts import NEW_WORLDBUILDING_SYSTEM
    return NEW_WORLDBUILDING_SYSTEM


def get_chapter_evaluation_prompt() -> str:
    """Chapter evaluation prompt — same as internal evaluate_chapter tool."""
    from .chapter_evaluation_prompts import CHAPTER_EVALUATION_SYSTEM
    return CHAPTER_EVALUATION_SYSTEM


def get_conflict_suggestion_prompt() -> str:
    """Conflict suggestion prompt — same as internal suggest_conflicts tool."""
    from .analysis_prompts import CONFLICT_SUGGESTION_SYSTEM
    return CONFLICT_SUGGESTION_SYSTEM


def get_public_chapter_quality_system_prompt() -> str:
    """Build the public chapter_writing_quality pack system prompt.

    Uses the SAME modules as the internal chapter_quality.py pack.
    Edit these modules once — both internal and external agents benefit.
    """
    from .anti_ai_prompts import build_anti_ai_system_prompt
    from .craft_prompts import build_craft_system_prompt
    from .dialogue_prompts import build_dialogue_system_prompt

    return (
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
        f"{build_craft_system_prompt()}\n\n"
        f"{build_dialogue_system_prompt()}\n\n"
        f"{build_anti_ai_system_prompt()}\n\n"
        "【风格设定】\n{style_context}"
    )


def get_public_chapter_fast_system_prompt() -> str:
    """Build the public chapter_writing_fast pack system prompt.

    Uses the SAME modules as the internal chapter_fast.py pack.
    Edit these modules once — both internal and external agents benefit.
    """
    from .anti_ai_prompts import TIER1_BANNED_WORDS, FORBIDDEN_SENTENCE_TEMPLATES
    from .body_emotion_replacement import BODY_EMOTION_REPLACEMENT
    from .scene_weaving import SCENE_WEAVING_RULE
    from .dialogue_prompts import build_dialogue_system_prompt

    tier1_words = []
    for category_words in TIER1_BANNED_WORDS.values():
        tier1_words.extend(category_words)
    forbidden_templates = "\n".join(f"- {name}：{example}" for name, example in FORBIDDEN_SENTENCE_TEMPLATES[:8])

    return (
        "你是一位小说写手。根据提供的大纲和项目上下文，快速写出流畅的章节正文。\n\n"
        "【任务】\n"
        "写出 1500-2000 字的章节正文。快速模式优先速度，不走完整质量评估流水线。\n\n"
        "【写作原则】\n"
        "1. 大纲是你的骨架——其中指定的场景和冲突必须被遵守。\n"
        "2. 叙事视角和文风严格遵循【风格设定】。\n"
        "3. 短句、动作描写、感官细节优先。\n\n"
        "【输出格式】\n"
        "只输出章节正文本身。不要加前言、后记或元评论。\n\n"
        f"【禁用词】\n{'、'.join(tier1_words)}\n\n"
        f"【禁用句式模板】\n{forbidden_templates}\n\n"
        f"{build_dialogue_system_prompt()}\n\n"
        f"{BODY_EMOTION_REPLACEMENT}\n\n"
        f"{SCENE_WEAVING_RULE}\n\n"
        "【风格设定】\n{style_context}"
    )


def get_naming_resolution_rules() -> str:
    """Get naming/alias resolution rules for cataloging."""
    return (
        "【称呼消歧规则】\n"
        "1. 种族/群体名 vs 个体名：\n"
        "   - 如果\"血魔\"指整个种族，用 worldbuilding_create 记录种族设定\n"
        "   - 如果\"血魔\"指某个有名字的个体，用 character_create 创建角色，name 用真名\n"
        "   - 在 aliases 中同时记录种族名和个体称呼，便于后续消歧\n"
        "   - 例：角色名\"血魔君\"，aliases=[\"血魔\", \"血魔君\", \"那个血魔\"]\n\n"
        "2. 称呼模糊时的判断依据：\n"
        "   - 有独立行动和对话 → 个体角色\n"
        "   - 作为背景/群体出现 → 种族设定\n"
        "   - 先是群体后来暴露为个体 → 先建种族设定，后建角色，关联两者\n\n"
        "3. 多角色同名/同称呼：\n"
        "   - 用 description 字段区分：\"第一个出场的血魔\"、\"血魔族长\"\n"
        "   - 在 aliases 中记录所有已知称呼\n"
        "   - 等明确身份后再用 character_update 修正\n\n"
        "4. 尊称/职位 vs 真名：\n"
        "   - name 字段放最稳定的主名称（真名或最常用名）\n"
        "   - aliases 放所有其他称呼：尊称、昵称、职位、代号\n"
        "   - 例：name=\"张三\"，aliases=[\"张大人\", \"三哥\", \"那个书生\"]"
    )
