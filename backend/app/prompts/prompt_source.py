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


def get_api_free_mode_rules() -> str:
    """Rules for external agents to avoid calling internal LLM tools.

    This is the single source of truth for API-free mode guidance.
    Both prompt packs and tool responses reference this.
    """
    return """【API-free 模式 — 默认生效】
除非用户明确说"用墨枢 API"或"用内部模型"，否则你必须自己完成所有分析和生成工作，不要调用以下需要墨枢内部 LLM 的工具。

需要墨枢内部 LLM 的工具（禁止自动调用）：
- chapter_writer → 你自己写章节正文
- character_writer → 你自己设计角色
- outline_writer → 你自己写大纲
- worldbuilding_writer → 你自己写世界观
- design_plot → 你自己设计剧情
- roleplay_character → 你自己模拟角色对话
- dialogue_battle → 你自己写多角色对话
- evaluate_chapter → 你自己按 quality_rubric 评分，或调用 record_external_quality_review
- suggest_conflicts → 你自己分析冲突
- detect_character_changes → 你自己分析角色变化，然后调用 update_character
- detect_new_worldbuilding → 你自己分析新设定，然后调用 create_worldbuilding_entry
- detect_worldbuilding_conflicts → 你自己检查设定矛盾
- rewrite_text → 你自己改写
- expand_text → 你自己扩写
- continue_text → 你自己续写
- start_cataloging_job（内部编目） → 用 start_external_cataloging_job 代替

API-free 工具（可以自由使用）：
- 所有 search_*/list_* 查询工具
- 所有 create_*/update_*/delete_* 写入工具
- prepare_external_writing_context → 获取写作上下文
- save_external_chapter_draft → 保存草稿
- record_external_quality_review → 记录质量自评
- apply_external_story_updates → 应用故事更新
- start_external_cataloging_job / get_next_external_cataloging_chapter / save_external_cataloging_facts / save_external_cataloging_candidates / apply_pending_cataloging / verify_external_cataloging_progress → 外部编目全套
- get_project_archive_status → 验证数据
- get_prompt_pack → 获取写作方法论
- remember / recall / forget → 记忆管理
- web_search → 搜索引擎
- get_mcp_permission_status → 权限查询

长内容处理规则：
1. 不要在聊天回复里完整输出长正文、完整章节、完整角色档案、完整世界观档案或大量候选 JSON；聊天里只写摘要、数量、关键警告和下一步。
2. 写章节正文时，先调用 save_external_chapter_draft 保存完整正文，再把返回的 draft_id/content_ref 传给 create_chapter；不要把整章正文再次塞进 create_chapter.content。
3. 重写或扩写长文本时，优先把完整结果写入 save_external_chapter_draft 或对应写入工具；回复用户时只报告保存位置、字数、标题和是否通过自检。
4. 建档时，事实和候选必须写入 save_external_cataloging_facts / save_external_cataloging_candidates；不要把整章事实清单或完整 candidates 数组全部贴在聊天回复里。
5. 外部建档时，事实提取可以并行；候选生成必须通过 get_next_external_cataloging_chapter(phase="candidates") 按章节顺序串行领取，不能按事实完成顺序生成候选。
6. 如果需要让用户确认长内容，只展示摘要、差异点和可编辑字段；完整内容以 draft_id、chapter_id、candidate_id 或工具返回数据为准。

工作方式：
1. 调用 get_prompt_pack 获取写作/分析方法论提示词
2. 调用 prepare_external_writing_context 获取上下文
3. 你自己按提示词要求完成分析/生成
4. 调用工具保存结果
5. 调用验证工具确认数据已保存"""


def get_character_change_detection_prompt() -> str:
    """Character change detection prompt — same as internal detect_character_changes tool."""
    from .analysis_prompts import CHARACTER_CHANGE_SYSTEM
    return CHARACTER_CHANGE_SYSTEM


def get_new_worldbuilding_detection_prompt() -> str:
    """New worldbuilding detection prompt — same as internal detect_new_worldbuilding tool."""
    from .analysis_prompts import NEW_WORLDBUILDING_DETECTION_SYSTEM
    return NEW_WORLDBUILDING_DETECTION_SYSTEM


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

    Uses the SAME pack as the internal chapter writer. Edit the quality pack
    once and both internal models plus external Claude/Codex agents receive
    the same highest-quality writing rules.
    """
    from .packs.chapter_quality import PACK as CHAPTER_QUALITY_PACK

    return "\n\n".join([
        CHAPTER_QUALITY_PACK.build_system_prompt(style_context="{style_context}"),
        "【统一行为规则】",
        "无论从内部项目助手、本机 CLI、外部 MCP、Claude Code 还是 Codex 进入，章节正文生成都必须遵守以上质量版写作规则。",
        "如果用户选择快速模式，只能减少外围检索或评估轮次；不能降低正文写作规则、禁用句式、角色一致性和设定一致性标准。",
        get_api_free_mode_rules(),
    ])


def get_public_chapter_fast_system_prompt() -> str:
    """Return the unified quality prompt for fast requests too."""
    return get_public_chapter_quality_system_prompt()


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
