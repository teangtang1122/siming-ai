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
        CHAPTER_END_BAN_PATTERNS,
    )

    patterns: list[str] = []
    # Tier 1 words
    for category_words in TIER1_BANNED_WORDS.values():
        patterns.extend(category_words)
    # Tier 2 threshold words
    patterns.extend(TIER2_THRESHOLD_WORDS)
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
        "【时间追踪规则】\n"
        "1. 普通时间推进：章节间有\"三天后\"、\"一个月后\"等描述时，更新角色 age 字段。\n"
        "2. 平行时间线：角色在幻境/穿越/异空间度过了很长时间，但主世界时间线未变时：\n"
        "   - age 字段记录角色的主观经历年龄（如\"外表3岁，实际经历约100年\"）\n"
        "   - character_timeline 记录时间异常事件，event_type 用 status_change\n"
        "   - 在 character_state 的 mental_state 中注明时间感知差异\n"
        "3. 时间回溯/重置：如果故事有时间回溯，记录回溯前后的年龄差异\n"
        "4. 冻结/停止衰老：如果角色有不老体质，在 age 字段注明（如\"外表16岁，实际200岁\"）"
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
