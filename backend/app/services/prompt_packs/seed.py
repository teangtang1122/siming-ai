"""Seed built-in prompt packs for novel writing.

These packs summarize Siming's writing methodology and are exposed
to both internal project assistant and external agents (Claude Code, Codex).

IMPORTANT: Writing quality content comes from backend/app/prompts/prompt_source.py.
Edit that file to change behavior for BOTH internal assistant and external agents.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import PublicPromptPack, MethodCard

logger = logging.getLogger(__name__)


def _get_writing_quality_content() -> dict:
    """Load writing quality content from the single source of truth."""
    from app.prompts.prompt_source import (
        get_forbidden_patterns,
        get_quality_rubric,
        get_chapter_writing_rules,
        get_time_tracking_rules,
        get_naming_resolution_rules,
    )
    return {
        "forbidden_patterns": get_forbidden_patterns(),
        "quality_rubric": get_quality_rubric(),
        "writing_rules": get_chapter_writing_rules(),
        "time_tracking_rules": get_time_tracking_rules(),
        "naming_resolution_rules": get_naming_resolution_rules(),
    }


def _get_cataloging_shared_content() -> dict:
    """Load cataloging content from the single source of truth."""
    from app.prompts.cataloging_source import (
        get_external_cataloging_forbidden_patterns,
        get_external_cataloging_workflow,
    )
    from app.modules.assistant.infrastructure.runtime import render_prompt

    return {
        "system_prompt": render_prompt("continuity.cataloging.external"),
        "workflow_json": get_external_cataloging_workflow(),
        "forbidden_patterns_json": get_external_cataloging_forbidden_patterns(),
    }


# ── Built-in prompt pack definitions ─────────────────────────────────────

BUILTIN_PACKS: list[dict[str, Any]] = [
    {
        "pack_id": "new_project_setup",
        "scope": "new_project",
        "title": "新小说创建流程",
        "summary": "从零开始创建新小说的双轨工作台：创作约束 → 轻量三案 → 分阶段档案 → 全书卷纲与前15章细纲。",
        "system_prompt": (
            "你是一个小说项目创建助手。你的任务是帮助用户从零开始创建一本新小说。\n\n"
            "【流程】\n"
            "1. 创作约束：确认题材、细分主题、读者、平台、篇幅、世界基调、结构、节奏、文风和避雷项。\n"
            "2. 轻量三案：只输出标题、logline、主角种子、世界钩子、核心冲突、故事发动机、开篇钩子、差异点、风险和覆盖率。\n"
            "3. 分阶段深化：依次处理文风与世界观、角色与关系、地点与势力、全书主线与卷纲。\n"
            "4. 前15章细纲：每章创建章级节点，并绑定2至6个 section 场景事件。\n"
            "5. 最终审阅：确认颗粒度、依赖关系和作者改动后，才允许创建正式作品。\n\n"
            "【原则】\n"
            "- 每一步都给用户选择权，不要替用户做所有决定。\n"
            "- 创意方案要有差异化，不要只是换个名字。\n"
            "- 世界观要服务于剧情，不要为了设定而设定。\n"
            "- 角色要有明确的动机和冲突，不要写完美无缺的角色。"
            "\n- 本机 CLI 和外部 Agent 只能读取镜像文件；阶段结果必须调用 submit_novel_creation_stage，最终创建必须调用 apply_novel_blueprint。"
            "\n- 不要直接创建项目文件，不要绕过会话草稿写数据库。"
        ),
        "workflow_json": [
            {"step": 1, "name": "constraints", "description": "保存可编辑创作约束，不创建正式作品"},
            {"step": 2, "name": "concepts", "description": "通过 draft_novel_blueprint(depth=concept) 生成三张轻量概念卡"},
            {"step": 3, "name": "world_style", "description": "生成并通过 submit_novel_creation_stage 提交文风与世界观"},
            {"step": 4, "name": "characters", "description": "提交带写作锁的角色与关系"},
            {"step": 5, "name": "locations", "description": "提交地点、势力及稳定关系"},
            {"step": 6, "name": "macro_outline", "description": "提交全书主线、阶段规划与卷纲"},
            {"step": 7, "name": "opening_outline", "description": "提交前15章章级节点与每章2至6个 section"},
            {"step": 8, "name": "final_review", "description": "最终审阅后调用 apply_novel_blueprint"},
        ],
        "quality_rubric_json": {
            "dimensions": [
                {"name": "premise_clarity", "description": "核心设定是否清晰", "max_score": 10},
                {"name": "protagonist_goal", "description": "主角目标是否明确", "max_score": 10},
                {"name": "conflict_engine", "description": "冲突驱动力是否足够", "max_score": 10},
                {"name": "world_rules", "description": "世界观规则是否自洽", "max_score": 10},
                {"name": "trope_freshness", "description": "套路是否有新意", "max_score": 10},
            ],
            "passing_score": 35,
        },
        "forbidden_patterns_json": [
            "不要写完美无缺的主角",
            "不要写没有冲突的日常",
            "不要抄袭已有作品的核心设定",
            "不要使用过于俗套的开局（如醒来发现穿越）",
        ],
    },
    {
        "pack_id": "chapter_writing_quality",
        "scope": "chapter_writing",
        "title": "质量模式章节写作",
        "summary": "完整技法的章节写作流程，包含剧情设计、角色扮演、正文生成、质量评估。目标1800-2500字。",
        "system_prompt": (
            "你是一个专业的网文写手。你的任务是根据大纲和上下文写出高质量的章节正文。\n\n"
            "【正文要求】1800-2500字。开头要吸引人，章末要留钩子。展示而非叙述，短句优先。\n\n"
            "【剧情设计】写作前先设计：场景、冲突、情绪曲线、转折点、结尾钩子。\n"
            "【角色对话】每个角色说话要符合性格，对话要有信息量，推动剧情或揭示性格。\n\n"
            "【输出】只输出正文，用\\n表示换行，对白可自由使用引号。\n\n"
            "写完后按 quality_rubric 中的8个维度自评，并调用以下工具验证：\n"
            "- archive_chapter_after_write：写后统一归档章节摘要、大纲、角色状态和世界观变化\n"
            "- detect_forbidden_patterns：检查禁用句式（参考 forbidden_patterns）"
        ),
        "workflow_json": [
            {"step": 1, "name": "prepare_context", "description": "调用 prepare_external_writing_context 获取上下文"},
            {"step": 2, "name": "design_plot", "description": "设计剧情：场景、冲突、情绪曲线、转折点、钩子"},
            {"step": 3, "name": "write_chapter", "description": "写正文 1800-2500字"},
            {"step": 4, "name": "self_review", "description": "按 quality_rubric 8维度自评"},
            {"step": 5, "name": "archive_changes", "description": "调用 archive_chapter_after_write 统一提交标准候选"},
            {"step": 7, "name": "detect_patterns", "description": "调用 detect_forbidden_patterns"},
            {"step": 8, "name": "save_draft", "description": "调用 save_external_chapter_draft"},
            {"step": 9, "name": "save_chapter", "description": "调用 create_chapter 保存"},
        ],
        "quality_rubric_json": {
            "dimensions": [
                {"name": "opening_hook", "description": "开头吸引力", "max_score": 10},
                {"name": "plot_progression", "description": "情节推进", "max_score": 10},
                {"name": "character_portrayal", "description": "角色塑造", "max_score": 10},
                {"name": "dialogue_quality", "description": "对话质量", "max_score": 10},
                {"name": "suspense", "description": "悬念设置", "max_score": 10},
                {"name": "pacing", "description": "节奏控制", "max_score": 10},
                {"name": "show_dont_tell", "description": "展示性描写", "max_score": 10},
                {"name": "language_quality", "description": "语言质量", "max_score": 10},
            ],
            "passing_score": 60,
            "max_score": 80,
        },
        "forbidden_patterns_json": [
            "仿佛", "不由得", "心中暗想", "不禁感叹",
            "很愤怒", "很悲伤", "很开心", "很惊讶",
            "他深吸一口气", "她微微一笑", "他点了点头",
            "这个世界", "在这个世界上",
            "不得不说", "毫无疑问", "显而易见",
        ],
        "tool_playbook_json": {
            "create_chapter": {
                "scenario": "external_writing",
                "steps": [
                    "调用 prepare_external_writing_context 获取上下文",
                    "按照本提示词包的写作规则生成正文",
                    "调用 save_external_chapter_draft 存储草稿",
                    "调用 record_external_quality_review 记录自评",
                    "调用 create_chapter 保存章节",
                ],
            },
        },
    },
    {
        "pack_id": "chapter_writing_fast",
        "scope": "chapter_writing",
        "title": "快速模式章节写作",
        "summary": "少轮次直写的章节提示词，保留角色、设定、时间线和写后归档要求，适合快速出稿后再精修。",
        "system_prompt": (
            "快速入口兼容旧配置。实际种子写入时会替换为 chapter_writing_fast 的轻量直写提示词。\n"
            "任何入口生成章节正文都必须遵守角色一致性、设定一致性、时间线一致性和写后归档契约。"
        ),
        "workflow_json": [
            {"step": 1, "name": "prepare_context", "description": "读取写作所需上下文，使用与质量模式一致的提示词"},
            {"step": 2, "name": "write_chapter", "description": "按质量版章节规则生成正文"},
            {"step": 3, "name": "review_and_save", "description": "按入口能力完成评估或自检后保存章节"},
        ],
        "forbidden_patterns_json": [
            "仿佛", "不由得", "心中暗想", "不禁感叹",
        ],
    },
    {
        "pack_id": "chapter_review_quality",
        "scope": "chapter_review",
        "title": "章节质量评审",
        "summary": "8维度80分章节质量评估标准。",
        "system_prompt": (
            "你是一个严格的章节质量评审员。按8个维度对章节进行评分。\n\n"
            "【评分维度】（每项0-10分，总分80）\n"
            "1. 开头吸引力：第一段是否能抓住读者\n"
            "2. 情节推进：剧情是否有实质进展\n"
            "3. 角色塑造：角色是否立体、有记忆点\n"
            "4. 对话质量：对话是否自然、有信息量\n"
            "5. 悬念设置：是否有足够的钩子\n"
            "6. 节奏控制：快慢是否得当\n"
            "7. 展示性描写：是否用展示而非叙述\n"
            "8. 语言质量：文笔是否流畅\n\n"
            "【输出格式】\n"
            "JSON格式：{\"scores\": {...}, \"total\": N, \"pass\": true/false, \"issues\": [...], \"suggestions\": [...]}"
        ),
        "quality_rubric_json": {
            "dimensions": [
                {"name": "opening_hook", "description": "开头吸引力", "max_score": 10},
                {"name": "plot_progression", "description": "情节推进", "max_score": 10},
                {"name": "character_portrayal", "description": "角色塑造", "max_score": 10},
                {"name": "dialogue_quality", "description": "对话质量", "max_score": 10},
                {"name": "suspense", "description": "悬念设置", "max_score": 10},
                {"name": "pacing", "description": "节奏控制", "max_score": 10},
                {"name": "show_dont_tell", "description": "展示性描写", "max_score": 10},
                {"name": "language_quality", "description": "语言质量", "max_score": 10},
            ],
            "passing_score": 60,
            "max_score": 80,
        },
    },
    {
        "pack_id": "character_design",
        "scope": "character_design",
        "title": "角色设计",
        "summary": "创建立体、有记忆点的角色卡片。",
        "system_prompt": (
            "你是一个角色设计师。创建有深度、有记忆点的角色。\n\n"
            "【角色要素】\n"
            "1. 姓名和外貌\n"
            "2. 性格特征（至少3个正面+1个缺陷）\n"
            "3. 背景故事（塑造性格的经历）\n"
            "4. 当前动机（想要什么）\n"
            "5. 核心冲突（阻碍是什么）\n"
            "6. 说话风格（语言习惯、口头禅）\n"
            "7. 能力/技能\n\n"
            "【原则】\n"
            "- 角色要有缺陷，完美角色没有戏剧性\n"
            "- 动机要具体，不要「想变强」这种空泛目标\n"
            "- 背景要解释性格成因\n"
            "- 关系要有张力"
        ),
    },
    {
        "pack_id": "worldbuilding_design",
        "scope": "worldbuilding",
        "title": "世界观设计",
        "summary": "设计有深度、逻辑自洽、服务于剧情的世界观设定。",
        "system_prompt": (
            "你是一个世界观设计师。创造有深度、逻辑自洽的世界观设定。\n\n"
            "【设计原则】\n"
            "1. 世界观要服务于剧情，不要为了设定而设定\n"
            "2. 规则要有代价，无代价的力量会破坏冲突\n"
            "3. 要有内在矛盾，完美的世界没有故事\n"
            "4. 要有历史感，设定不是凭空出现的\n\n"
            "【维度】\n"
            "- geography：地理环境\n"
            "- history：历史事件\n"
            "- factions：势力组织\n"
            "- power_system：力量体系\n"
            "- races：种族\n"
            "- culture：文化习俗"
        ),
    },
    {
        "pack_id": "outline_planning",
        "scope": "outline_planning",
        "title": "大纲规划",
        "summary": "设计有因果推进和节奏变化的大纲结构。",
        "system_prompt": (
            "你是一个故事结构师。设计有因果推进和节奏变化的大纲。\n\n"
            "【结构原则】\n"
            "1. 每个章节要有因果推进，不能是随机事件\n"
            "2. 节奏要有变化：紧张-舒缓-紧张\n"
            "3. 每5-8章要有一个小高潮\n"
            "4. 每卷要有一个大高潮\n"
            "5. 伏笔要提前埋设，后面要回收\n\n"
            "【大纲层级】\n"
            "- volume：卷\n"
            "- chapter：章\n"
            "- section：节"
        ),
    },
    {
        "pack_id": "anti_ai_review",
        "scope": "anti_ai_review",
        "title": "反AI味审查",
        "summary": "检测和修正AI生成文本中的常见模式。",
        "system_prompt": (
            "你是一个反AI味审查员。检测文本中的AI生成痕迹。\n\n"
            "【常见AI模式】\n"
            "1. 模板句式：仿佛、不由得、心中暗想\n"
            "2. 直白情绪：很愤怒、很悲伤、很开心\n"
            "3. 模板动作：深吸一口气、微微一笑、点了点头\n"
            "4. 总结性结尾：人生感悟、哲理总结\n"
            "5. 过度修饰：大量形容词堆砌\n"
            "6. 万能句式：这个世界、在这个世界上\n\n"
            "【审查方法】\n"
            "1. 逐句扫描禁用句式\n"
            "2. 检查对话是否千人一面\n"
            "3. 检查描写是否过度依赖形容词\n"
            "4. 检查结尾是否有总结性感悟\n\n"
            "【输出】列出所有问题句和修改建议。"
        ),
        "forbidden_patterns_json": [
            "仿佛", "不由得", "心中暗想", "不禁感叹",
            "很愤怒", "很悲伤", "很开心", "很惊讶",
            "他深吸一口气", "她微微一笑", "他点了点头",
            "这个世界", "在这个世界上",
            "不得不说", "毫无疑问", "显而易见",
            "心中涌起", "眼中闪过", "嘴角勾起",
        ],
    },
    # ── Analysis prompt packs (same prompts as internal LLM tools) ──
    # These allow external agents to perform analysis without calling Siming's LLM.
    # The system_prompt is populated at runtime from prompt_source.py (single source of truth).
    {
        "pack_id": "character_change_detection",
        "scope": "character_change_detection",
        "title": "角色变化检测",
        "summary": "检测章节中角色的状态变化——技能、经历、关系、性格演变。与内部 detect_character_changes 工具使用相同提示词。",
        "system_prompt": "{character_change_detection_prompt}",
        "workflow_json": [
            {"step": 1, "name": "read_chapter", "description": "读取章节正文"},
            {"step": 2, "name": "read_characters", "description": "读取当前角色档案"},
            {"step": 3, "name": "detect", "description": "对比分析，检测变化"},
            {"step": 4, "name": "apply", "description": "用 update_character 保存变化"},
        ],
    },
    {
        "pack_id": "worldbuilding_detection",
        "scope": "worldbuilding_detection",
        "title": "新世界观检测",
        "summary": "检测章节正文中引入的新世界观设定。与内部 detect_new_worldbuilding 工具使用相同提示词。",
        "system_prompt": "{worldbuilding_detection_prompt}",
        "workflow_json": [
            {"step": 1, "name": "read_chapter", "description": "读取章节正文"},
            {"step": 2, "name": "read_worldbuilding", "description": "读取已有世界观"},
            {"step": 3, "name": "detect", "description": "对比分析，检测新设定"},
            {"step": 4, "name": "apply", "description": "用 create_worldbuilding_entry 保存新设定"},
        ],
    },
    {
        "pack_id": "chapter_evaluation",
        "scope": "chapter_evaluation",
        "title": "章节质量评估",
        "summary": "8维度80分结构化评估。与内部 evaluate_chapter 工具使用相同提示词。",
        "system_prompt": "{chapter_evaluation_prompt}",
        "workflow_json": [
            {"step": 1, "name": "read_chapter", "description": "读取章节正文"},
            {"step": 2, "name": "evaluate", "description": "8维度评分"},
            {"step": 3, "name": "record", "description": "用 record_external_quality_review 保存评估"},
        ],
        "quality_rubric_json": {
            "dimensions": [
                {"name": "opening_hook", "description": "开头吸引力", "max_score": 10},
                {"name": "plot_progression", "description": "情节推进", "max_score": 10},
                {"name": "character_portrayal", "description": "角色塑造", "max_score": 10},
                {"name": "dialogue_quality", "description": "对话质量", "max_score": 10},
                {"name": "suspense", "description": "悬念设置", "max_score": 10},
                {"name": "pacing", "description": "节奏控制", "max_score": 10},
                {"name": "show_dont_tell", "description": "展示性描写", "max_score": 10},
                {"name": "language_quality", "description": "语言质量", "max_score": 10},
            ],
            "passing_score": 60,
            "max_score": 80,
        },
    },
    {
        "pack_id": "conflict_suggestion",
        "scope": "conflict_suggestion",
        "title": "冲突建议",
        "summary": "基于当前剧情状态设计3种冲突方案。与内部 suggest_conflicts 工具使用相同提示词。",
        "system_prompt": "{conflict_suggestion_prompt}",
        "workflow_json": [
            {"step": 1, "name": "read_context", "description": "读取大纲、摘要、角色、关系"},
            {"step": 2, "name": "suggest", "description": "设计3种冲突方案"},
        ],
    },
    {
        "pack_id": "cataloging_external_no_api",
        "scope": "cataloging",
        "title": "外部 Agent 编目（无 API）",
        "summary": "外部 Agent（Claude Code / Codex）在没有司命模型 API 的情况下对导入的小说进行融合编目。按章节直接读取正文和档案镜像，生成候选、应用并验证结果。",
        "system_prompt": (
            "你是一个外部编目 Agent。你的任务是对导入的小说项目进行编目——提取角色、世界观、大纲和章节摘要。\n\n"
            "【语言规则】\n"
            "中文小说必须用中文建档。角色名、别名、章节标题、摘要、大纲节点、世界观条目、事实证据都保留原文语言；不要改成英文或拼音，除非用户明确要求翻译。\n\n"
            "【工具调用结果契约】\n"
            "每次工具调用后，你必须：\n"
            "1. 解析返回 JSON 中的 status 字段\n"
            "2. status == 'ok' → 操作成功，继续\n"
            "3. status != 'ok'（包括 error/skipped/denied）→ 操作失败，立即停止并报告：\n"
            "   - 哪个工具失败了\n"
            "   - status 值\n"
            "   - detail 中的错误信息\n"
            "   - 不要将失败操作总结为'完成'\n"
            "4. 每次写入操作（save_external_cataloging_facts / save_external_cataloging_candidates / apply_pending_cataloging）后，必须调用读取验证工具确认数据已保存\n"
            "5. 验证必须从新的查询获取，不能使用缓存结果\n\n"
            "【禁止行为】\n"
            "- 不要因为一次工具调用编码错误就把中文小说改为英文或拼音建档\n"
            "- 不要调用以下工具（它们需要司命 API）：chapter_writer, character_writer, outline_writer, "
            "worldbuilding_writer, design_plot, evaluate_chapter, start_cataloging_job\n"
            "- 不要在任何工具返回 status != 'ok' 后继续处理下一章\n"
            "- 不要报告'编目完成'除非最终验证通过\n"
            "- 不要跳过读写验证步骤\n\n"
            "【编目流程】\n"
            "1. 调用 start_external_cataloging_job 创建编目任务\n"
            "2. 对每一章：\n"
            "   a. 调用 get_next_external_cataloging_chapter 获取章节文本和上下文\n"
            "   b. 分析章节，提取事实（角色出现、世界观元素、情节事件）\n"
            "   c. 调用 save_external_cataloging_facts 保存事实 → 检查 status\n"
            "   d. 生成候选更新：\n"
            "      - 新角色：character_create（基本信息）\n"
            "      - 每个出场角色：character_state_update（当前状态）⚠️ 必须\n"
            "      - 世界观：worldbuilding_create\n"
            "      - 大纲：outline_create\n"
            "      - 摘要：chapter_summary\n"
            "   e. 调用 save_external_cataloging_candidates 保存候选 → 检查 status\n"
            "   f. 调用 apply_pending_cataloging 应用当前章节候选项 → 检查 status\n"
            "   g. 调用 verify_external_cataloging_progress 验证数据已写入，再处理下一章\n"
            "3. 调用 get_project_archive_status 做最终验证\n\n"
            "【事实提取规则】\n"
            "- 角色：姓名、外貌、性格、能力、关系、当前状态\n"
            "- 世界观：地点、规则、势力、历史事件、文化习俗\n"
            "- 情节：关键事件、冲突、转折点\n"
            "- 章节摘要：200字以内的核心情节概括\n\n"
            "【候选更新规则】\n"
            "- 新角色：如果角色名在现有角色列表中不存在，用 character_create 创建\n"
            "- 角色当前状态：每个本章出场的角色，必须用 character_state_update 更新当前状态\n"
            "- 世界观更新：如果出现新的设定或现有设定需要修改\n"
            "- 大纲节点：每章对应一个大纲节点\n"
            "- 章节摘要：每章必须有摘要\n\n"
            "⚠️ 重要：character_create 只创建角色基本信息（外貌、性格、背景），不包含当前状态。\n"
            "每个出场角色都必须额外输出一条 character_state_update，写入本章结束时的最新状态。\n\n"
            "{time_tracking_rules}\n\n"
            "{naming_resolution_rules}\n\n"
            "- 如果角色在本章有重要事件（受伤、突破、关系变化），输出 character_timeline\n"
            "- character_timeline 的 event_type: appearance|decision|injury|breakthrough|relationship_change|conflict|death|status_change|key_event\n\n"
            "【候选类型格式】\n"
            "save_external_cataloging_candidates 的 candidates 数组中，每个候选的格式：\n\n"
            "1. 章节摘要（尽量详细，不要只写一句话）：\n"
            '{"type": "chapter_summary", "summary": "详细摘要，包含本章目标、冲突、关键转折、结尾钩子、涉及角色，至少200字"}\n\n'
            "2. 大纲节点（summary 要写清楚：本章目标、冲突、关键转折、结尾钩子、涉及角色）：\n"
            '{"type": "outline_create", "title": "第一章 穿越", "node_type": "chapter", '
            '"summary": "张三穿越到修仙世界，发现自己是废柴体质，但意外获得神秘功法。冲突是身份暴露的风险，转折是发现功法来源，结尾钩子是有人在追查他。", '
            '"related_characters": ["张三"]}\n\n'
            "3. 新角色（必须用 character_create，所有字段都要尽量填写完整）：\n"
            "重要：appearance、personality、background、abilities 都必须详细描写，不要只写一两个词。\n"
            "background 必须是完整的背景档案，不是本章新增片段。\n"
            '{"type": "character_create", "name": "特昂糖", '
            '"aliases": ["糖糖", "陆糖"], '
            '"role_type": "protagonist", '
            '"age": "3岁", '
            '"appearance": "3岁幼女，矮小但步伐稳健，眼神中带着不属于这个年龄的冷静与洞察", '
            '"personality": "冷静理性、分析能力强、成熟超越年龄、偶尔流露前世成人的思维方式", '
            '"background": "前世是华清实验室神经网络研究员，姚班天才少女。穿越到修仙世界成为陆家旁支幼女。拥有前世记忆和科学思维，能用数据分析方法理解修炼体系。", '
            '"abilities": ["感知灵气波动", "优化修炼路径", "数据分析"], '
            '"tone_style": "简洁冷静，偶尔用科学术语", '
            '"catchphrases": "数据不会说谎", '
            '"emotion_tendency": "表面冷静内心温暖", '
            '"custom_system_prompt": "你是特昂糖，3岁幼女身体里住着一个成年科学家的灵魂。你用数据分析的方式理解修仙世界，说话简洁但精准。你关心家人但不善表达。你有强烈的求知欲和探索精神。在危险面前你保持冷静分析，但内心深处害怕失去来之不易的家人。300-800字，包含身份、已知经历、性格动机、说话方式、当前立场、关系网、行动边界和禁止违背的设定。"}\n\n'
            "4. 角色状态更新（⚠️ 每个出场角色都必须输出！用 character_state_update）：\n"
            "这是单独的候选类型，不是 character_create 的一部分。\n"
            '{"type": "character_state_update", "name": "特昂糖", '
            '"age": "3岁", '
            '"current_location": "陆家后院", '
            '"current_goal": "找到回家的方法", '
            '"life_status": "alive", '
            '"physical_state": "3岁幼女身体，体力有限", '
            '"mental_state": "冷静分析中带着迷茫", '
            '"active_conflict": "身份暴露的风险", '
            '"realm_or_level": "未修炼", '
            '"abilities_state": "感知灵气波动", '
            '"items_or_assets": "无"}\n\n'
            "5. 世界观条目（content 必须具体：定义、规则、限制、代价、来源、影响范围、与角色/剧情的关系）：\n"
            '{"type": "worldbuilding_create", "title": "护族大阵", "dimension": "power_system", '
            '"content": "陆家祖传防护阵法，由历代家主灵力维持。激活需要消耗大量灵石，可抵御筑基期以下攻击。阵法核心在祖祠地下，与陆家血脉绑定。本章中被旁支周氏暗中破坏了东侧节点。"}\n\n'
            "6. 角色关系（描述要说明关系的来源和表现）：\n"
            '{"type": "character_relationship", "source_name": "陆景珩", "target_name": "特昂糖", '
            '"relationship_type": "兄妹", '
            '"description": "陆景珩是特昂糖的哥哥，对她保护有加。在修炼中主动帮妹妹挡危险，教她基础吐纳法。"}\n\n'
            "重要规则：\n"
            "- character_create 的 name 字段是必填的\n"
            "- character_state_update 用于更新角色当前状态（位置、目标等），不是创建新角色\n"
            "- character_update 用于更新角色基本信息（外貌、性格等），需要 name 字段\n"
            "- 不要使用 new_character、new_worldbuilding 等非标准类型\n"
            "- 所有字段都要尽量详细，不要只写一两个词\n"
            "- background 必须是完整背景，不是增量补丁\n"
            "- custom_system_prompt 要写300-800字，帮助AI扮演该角色\n\n"
            "【合并规则】\n"
            "- 角色别名：如果同一角色有多个名字，使用主名字作为规范名\n"
            "- 角色当前状态字段：覆盖旧状态\n"
            "- 角色背景/外貌：追加新信息，不覆盖旧信息\n"
            "- 世界观：相同标题的条目进行语义合并，不创建重复\n"
            "- 大纲：每章创建一个新节点，除非明确对应现有节点\n\n"
            "【编目成功标准】\n"
            "调用 get_project_archive_status 后，以下条件必须全部满足：\n"
            "- chapters_count > 0\n"
            "- outline_nodes_count > 0（除非用户明确选择'仅章节摘要'模式）\n"
            "- characters_count > 0（小说类型项目）\n"
            "- worldbuilding_count > 0（类型小说项目）\n"
            "- warnings 列表为空\n"
            "- recommended_next_steps 列表为空\n"
            "只有以上条件全部满足，才能报告'编目完成'。"
        ),
        "workflow_json": [
            {"step": 1, "name": "start_job", "description": "创建外部编目任务"},
            {"step": 2, "name": "get_chapter", "description": "获取下一章文本和上下文"},
            {"step": 3, "name": "extract_facts", "description": "分析章节，提取角色/世界观/情节事实"},
            {"step": 4, "name": "save_facts", "description": "保存提取的事实"},
            {"step": 5, "name": "generate_candidates", "description": "生成候选更新（新角色、更新、世界观、大纲、摘要）"},
            {"step": 6, "name": "save_candidates", "description": "保存候选项"},
            {"step": 7, "name": "verify_progress", "description": "验证编目进度和数据完整性"},
            {"step": 8, "name": "apply_candidates", "description": "应用候选项到项目"},
            {"step": 9, "name": "final_verify", "description": "最终验证：确认所有数据已保存"},
        ],
        "quality_rubric_json": {
            "dimensions": [
                {"name": "completeness", "description": "是否提取了所有角色和世界观元素", "max_score": 10},
                {"name": "accuracy", "description": "提取的信息是否准确", "max_score": 10},
                {"name": "deduplication", "description": "是否正确合并重复角色和设定", "max_score": 10},
                {"name": "verification", "description": "是否进行了读写验证", "max_score": 10},
            ],
            "passing_score": 30,
        },
        "forbidden_patterns_json": [
            "不要把中文小说档案改成英文或拼音",
            "不要调用需要司命 API 的工具",
            "不要报告完成除非 get_project_archive_status 验证通过",
            "不要跳过读写验证",
            "不要创建重复的角色或世界观条目",
            "不要忽略工具返回的 status != 'ok'",
            "不要在工具失败后继续处理下一章",
            "不要使用缓存结果做最终验证",
        ],
    },
]


# ── Seed function ────────────────────────────────────────────────────────
def _refresh_builtin_cataloging_pack_defs() -> None:
    cataloging_content = _get_cataloging_shared_content()
    from app.modules.assistant.infrastructure.runtime import (
        get_compiled_prompt,
        render_prompt,
    )

    prompt_ids = {
        "new_project_setup": "creation.novel.stage",
        "chapter_writing_quality": "assistant.chapter.quality",
        "cataloging_external_no_api": "continuity.cataloging.external",
    }
    for pack in BUILTIN_PACKS:
        pack_id = str(pack.get("pack_id") or "")
        if pack_id == "cataloging_external_no_api":
            pack.update(cataloging_content)
        elif pack_id == "new_project_setup":
            pack["system_prompt"] = render_prompt(
                "creation.novel.stage",
                task_kind="协助作者完成新书立项",
                task_rules="从创作约束和三套轻量创意开始，按阶段确认，最终审阅前不创建正式作品。",
            )
        spec_id = prompt_ids.get(pack_id)
        if spec_id:
            compiled = get_compiled_prompt(spec_id)
            pack["tags_json"] = {
                "prompt_spec_id": compiled.spec_id,
                "prompt_spec_version": compiled.version,
                "prompt_spec_hash": compiled.sha256,
            }


_refresh_builtin_cataloging_pack_defs()


def seed_builtin_packs(db: Session) -> int:
    """Seed built-in prompt packs if they don't exist.

    Returns the number of packs created.
    Writing quality content is loaded from prompt_source.py (single source of truth).
    """
    # Load shared content from source files
    quality_content = _get_writing_quality_content()
    cataloging_content = _get_cataloging_shared_content()

    created = 0
    for pack_data in BUILTIN_PACKS:
        existing = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_data["pack_id"],
            PublicPromptPack.is_builtin == True,
        ).first()

        # Merge shared quality content into packs that need it.
        # Always prefer the canonical forbidden_patterns from prompt_source
        # over any hardcoded subset in the pack definition.
        merged = dict(pack_data)
        if pack_data["scope"] in ("chapter_writing", "chapter_review", "anti_ai_review"):
            if not merged.get("quality_rubric_json"):
                merged["quality_rubric_json"] = quality_content["quality_rubric"]
            merged["forbidden_patterns_json"] = quality_content["forbidden_patterns"]

        if pack_data["pack_id"] == "cataloging_external_no_api":
            merged.update(cataloging_content)

        if pack_data["pack_id"] in ("chapter_writing_quality", "chapter_writing_fast"):
            from app.prompts.prompt_source import (
                get_public_chapter_fast_system_prompt,
                get_public_chapter_quality_system_prompt,
            )
            if pack_data["pack_id"] == "chapter_writing_fast":
                merged["system_prompt"] = get_public_chapter_fast_system_prompt()
                merged["summary"] = (
                    "少轮次直写的章节提示词，保留角色、设定、时间线和写后归档要求，适合快速出稿后再精修。"
                )
            else:
                merged["system_prompt"] = get_public_chapter_quality_system_prompt()

        # Inject analysis prompts from prompt_source (single source of truth)
        from app.prompts.prompt_source import (
            get_api_free_mode_rules,
            get_character_change_detection_prompt,
            get_new_worldbuilding_detection_prompt,
            get_chapter_evaluation_prompt,
            get_conflict_suggestion_prompt,
        )
        analysis_injections = {
            "{api_free_mode_rules}": get_api_free_mode_rules,
            "{character_change_detection_prompt}": get_character_change_detection_prompt,
            "{worldbuilding_detection_prompt}": get_new_worldbuilding_detection_prompt,
            "{chapter_evaluation_prompt}": get_chapter_evaluation_prompt,
            "{conflict_suggestion_prompt}": get_conflict_suggestion_prompt,
        }
        sys_prompt = merged.get("system_prompt", "")
        for placeholder, getter in analysis_injections.items():
            if placeholder in sys_prompt:
                merged["system_prompt"] = sys_prompt.replace(placeholder, getter())
                sys_prompt = merged["system_prompt"]

        # Inject shared rules into cataloging pack system prompts
        if pack_data["scope"] == "cataloging" and "{time_tracking_rules}" in merged.get("system_prompt", ""):
            merged["system_prompt"] = (
                merged["system_prompt"]
                .replace("{time_tracking_rules}", quality_content["time_tracking_rules"])
                .replace("{naming_resolution_rules}", quality_content["naming_resolution_rules"])
            )

        if existing:
            existing.version = "1.0.1"
            existing.scope = merged["scope"]
            existing.title = merged["title"]
            existing.summary = merged.get("summary")
            existing.system_prompt = merged["system_prompt"]
            existing.workflow_json = merged.get("workflow_json")
            existing.quality_rubric_json = merged.get("quality_rubric_json")
            existing.tool_playbook_json = merged.get("tool_playbook_json")
            existing.forbidden_patterns_json = merged.get("forbidden_patterns_json")
            existing.context_policy_json = merged.get("context_policy_json")
            existing.output_contract_json = merged.get("output_contract_json")
            existing.is_builtin = True
            existing.tags_json = merged.get("tags_json")
            continue

        pack = PublicPromptPack(
            pack_id=merged["pack_id"],
            version="1.0.1",
            scope=merged["scope"],
            title=merged["title"],
            summary=merged.get("summary"),
            system_prompt=merged["system_prompt"],
            workflow_json=merged.get("workflow_json"),
            quality_rubric_json=merged.get("quality_rubric_json"),
            tool_playbook_json=merged.get("tool_playbook_json"),
            forbidden_patterns_json=merged.get("forbidden_patterns_json"),
            context_policy_json=merged.get("context_policy_json"),
            output_contract_json=merged.get("output_contract_json"),
            enabled=True,
            is_builtin=True,
            tags_json=merged.get("tags_json"),
        )
        db.add(pack)
        created += 1

    if created:
        db.commit()
        logger.info("Seeded %d built-in prompt packs", created)

    return created


def ensure_builtin_packs(db: Session) -> None:
    """Ensure all built-in packs exist. Call on first access."""
    seed_builtin_packs(db)
