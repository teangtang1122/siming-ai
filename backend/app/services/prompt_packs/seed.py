"""Seed built-in prompt packs for novel writing.

These packs summarize Siming's writing methodology and are exposed
to both internal project assistant and external agents (Claude Code, Codex).

IMPORTANT: Writing quality content comes from backend/app/prompts/prompt_source.py.
Edit that file to change behavior for BOTH internal assistant and external agents.
"""
from __future__ import annotations

from app.architecture.uow import commit_session

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import PublicPromptPack

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
        "summary": "从零开始创建新小说的双轨工作台：创作约束 → 单一创意方向 → 分阶段档案 → 全书卷纲与前3章细纲。",
        "system_prompt": (
            "你是一个小说项目创建助手。你的任务是帮助用户从零开始创建一本新小说。\n\n"
            "【流程】\n"
            "1. 创作约束：确认题材、细分主题、读者、平台、篇幅、世界基调、结构、节奏、文风和避雷项。\n"
            "2. 单一创意方向：只输出标题、logline、主角种子、世界钩子、核心冲突、故事发动机、开篇钩子、差异点、风险和覆盖率，并允许后续对话调整。\n"
            "3. 分阶段深化：依次处理文风与世界观、角色与关系、地点与势力、全书主线与卷纲。\n"
            "4. 前3章细纲：每章创建章级节点，并绑定2至6个 section 场景事件。\n"
            "5. 最终审阅：确认颗粒度、依赖关系和作者改动后，才允许创建正式作品。\n\n"
            "【原则】\n"
            "- 每一步都给用户选择权，不要替用户做所有决定。\n"
            "- 创意方案要有差异化，不要只是换个名字。\n"
            "- 世界观要服务于剧情，不要为了设定而设定。\n"
            "- 角色要有明确的动机和冲突，不要写完美无缺的角色。"
            "\n- API、本机 CLI 与外部 Agent 使用同一组会话工具；阶段结果通过结构化工具保存，最终创建调用 finalize_creation_session。"
            "\n- 不要直接创建项目文件，不要绕过会话草稿写数据库。"
        ),
        "workflow_json": [
            {"step": 1, "name": "constraints", "description": "保存可编辑创作约束，不创建正式作品"},
            {"step": 2, "name": "concepts", "description": "生成并保存一张可持续调整的轻量概念卡"},
            {"step": 3, "name": "world_style", "description": "生成并通过 patch_creation_artifact 保存文风与世界观"},
            {"step": 4, "name": "characters", "description": "提交带写作锁的角色与关系"},
            {"step": 5, "name": "locations", "description": "提交地点、势力及稳定关系"},
            {"step": 6, "name": "macro_outline", "description": "提交全书主线、阶段规划与卷纲"},
            {"step": 7, "name": "opening_outline", "description": "提交前3章章级节点与每章2至6个 section"},
            {"step": 8, "name": "final_review", "description": "最终审阅后调用 finalize_creation_session"},
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
        "summary": "聚焦一次正文生成的完整章节提示词；去除 AI 味和质量评审由独立操作按需执行。目标1800-2500字。",
        "system_prompt": (
            "你是一个专业的网文写手。你的任务是根据大纲和上下文写出高质量的章节正文。\n\n"
            "【正文要求】1800-2500字。开头要吸引人，章末要留钩子。展示而非叙述，短句优先。\n\n"
            "【剧情设计】写作前先设计：场景、冲突、情绪曲线、转折点、结尾钩子。\n"
            "【角色对话】每个角色说话要符合性格，对话要有信息量，推动剧情或揭示性格。\n\n"
            "【输出】只输出正文，用\\n表示换行，对白可自由使用引号。\n\n"
            "本任务只生成基础正文，不执行去除 AI 味改写或质量评分。保存未入库草稿后立即结束，"
            "正式保存和建档由作者在界面选择。"
        ),
        "workflow_json": [
            {"step": 1, "name": "prepare_context", "description": "调用 prepare_external_writing_context 建立精简写作基线"},
            {"step": 2, "name": "retrieve_context", "description": "模型按需调用 search_task_context，只查看候选短摘要"},
            {"step": 3, "name": "select_evidence", "description": "调用 submit_context_evidence 复核必要来源并取得首个精确上下文页"},
            {"step": 4, "name": "read_context", "description": "若仍有后续页，逐次原样复制 next_arguments 调用 prepare_task_context；末页才返回选择令牌"},
            {"step": 5, "name": "write_chapter", "description": "读完全部精确上下文后，在下一模型步骤一次生成基础正文 1800-2500字"},
            {"step": 6, "name": "save_draft", "description": "携带末页选择令牌调用 save_external_chapter_draft 保存未入库草稿并立即结束"},
        ],
        "quality_rubric_json": None,
        "forbidden_patterns_json": [],
        "tool_playbook_json": {
            "save_external_chapter_draft": {
                "scenario": "external_writing",
                "steps": [
                    "调用 prepare_external_writing_context 建立目标、文风和作者要求组成的精简基线",
                    "模型自行调用 search_task_context 检索本章需要的资料，只查看候选短摘要",
                    "模型复核后调用 submit_context_evidence，取得首个精确 context_page",
                    "若 context_page.has_more=true，逐次原样复制 next_arguments 调用 prepare_task_context；不得跳页、改哈希或改页大小，末页才取得 context_selection_token",
                    "在下一模型步骤按照本提示词包的写作规则生成正文",
                    "携带选择令牌调用 save_external_chapter_draft 存储草稿",
                    "立即结束本轮；正式保存和建档由作者在界面操作",
                ],
            },
        },
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
        "summary": "使用模型主动选材设计可由作者编辑确认、且尚未写入正式大纲的提案。",
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
            "- section：节\n\n"
            "逐页读完 submit_context_evidence 返回的精确 context_page，末页取得选择令牌后再生成。输出一份可编辑提案，"
            "不得直接创建正式大纲，也不得在同一回合继续写正文。"
        ),
        "workflow_json": [
            {"step": 1, "name": "prepare_context", "description": "调用 prepare_task_context(task_type=outline_planning) 建立位置与文风基线"},
            {"step": 2, "name": "retrieve_context", "description": "模型按需调用 search_task_context，只查看候选短摘要"},
            {"step": 3, "name": "select_evidence", "description": "调用 submit_context_evidence 取得首个精确 context_page"},
            {"step": 4, "name": "read_context", "description": "若仍有后续页，逐次原样复制 next_arguments 调用 prepare_task_context；末页才返回选择令牌"},
            {"step": 5, "name": "propose_outline", "description": "读完全部精确上下文后，在下一模型步骤生成可编辑节点"},
            {"step": 6, "name": "save_draft", "description": "调用 save_external_outline_draft 保存提案并立即结束"},
        ],
        "tool_playbook_json": {
            "save_external_outline_draft": {
                "scenario": "external_outline_planning",
                "steps": [
                    "查询真实父节点与插入位置，不使用界面选中项猜目标",
                    "调用 prepare_task_context(task_type=outline_planning) 建立精简基线",
                    "模型按需检索、复核并调用 submit_context_evidence",
                    "按 next_arguments 逐页读完 context_page，末页取得选择令牌",
                    "在下一模型步骤只使用完整精确上下文生成提案",
                    "携带同一 manifest 和选择令牌调用 save_external_outline_draft",
                    "立即结束本轮；正式大纲写入和后续正文由作者确认触发",
                ],
            },
        },
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
        "title": "外部 Agent 建档（无 API）",
        "summary": "使用统一建档数据契约，由外部 Agent 逐章生成候选、应用并验证。",
        # The canonical prompt compiler hydrates these fields immediately below.
        # Keeping only metadata here prevents a second, silently divergent workflow.
        "system_prompt": "",
        "workflow_json": [],
        "quality_rubric_json": {
            "dimensions": [
                {"name": "completeness", "description": "建档数据契约是否完整", "max_score": 10},
                {"name": "accuracy", "description": "档案是否有正文证据", "max_score": 10},
                {"name": "deduplication", "description": "实体是否正确归一与去重", "max_score": 10},
                {"name": "verification", "description": "写入后是否重新读取验证", "max_score": 10},
            ],
            "passing_score": 40,
        },
        "forbidden_patterns_json": [],
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
                task_rules="从创作约束和本轮实际需要的创意方向开始，按阶段确认，最终审阅前不创建正式作品。",
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

        # Review and de-AI packs receive the canonical rubric/patterns. Base
        # writing packs intentionally do not: those are separate user actions.
        merged = dict(pack_data)
        if pack_data["scope"] in ("chapter_review", "anti_ai_review"):
            if not merged.get("quality_rubric_json"):
                merged["quality_rubric_json"] = quality_content["quality_rubric"]
            merged["forbidden_patterns_json"] = quality_content["forbidden_patterns"]
        elif pack_data["scope"] == "chapter_writing":
            merged["quality_rubric_json"] = None
            merged["forbidden_patterns_json"] = []

        if pack_data["pack_id"] == "cataloging_external_no_api":
            merged.update(cataloging_content)

        if pack_data["pack_id"] == "chapter_writing_quality":
            from app.prompts.prompt_source import get_public_chapter_quality_system_prompt
            merged["system_prompt"] = get_public_chapter_quality_system_prompt()
            merged["summary"] = (
                "聚焦一次正文生成的完整章节提示词；去除 AI 味和质量评审由独立操作按需执行。"
            )

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
            existing.version = "1.1.0"
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
            version="1.1.0",
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
        commit_session(db)
        logger.info("Seeded %d built-in prompt packs", created)

    return created


def ensure_builtin_packs(db: Session) -> None:
    """Ensure all built-in packs exist. Call on first access."""
    seed_builtin_packs(db)
