"""Plan generators for predefined agent execution paths."""
from __future__ import annotations

import re
from typing import Any

from .plan_graph import PlanGraph, StepDef


# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------

_INTENT_CHAPTER = "chapter"
_INTENT_CHARACTER = "character"
_INTENT_WORLDBUILDING = "worldbuilding"
_INTENT_PROJECT_INIT = "project_init"
_INTENT_SCHEDULED_TASK = "scheduled_task"
_INTENT_SKILL = "skill"
_INTENT_PROJECT = "project"
_INTENT_EXPORT = "export"
_INTENT_DECONSTRUCT = "deconstruct"
_INTENT_CREATE_NOVEL = "create_novel"
_INTENT_EXTERNAL_WRITING = "external_writing"


def plan_fast_chapter(
    *,
    outline_node_id: str,
    requirements: str = "",
    involved_characters: list[str] | None = None,
) -> PlanGraph:
    """Fast chapter write path: search -> write -> save -> detect changes."""
    chars = involved_characters or []
    char_args = {"involved_characters": chars} if chars else {}

    steps: dict[str, StepDef] = {
        "search_outline": StepDef(
            tool="search_outline",
            args={"node_id": outline_node_id},
            depends_on=[],
            label="查找大纲节点",
        ),
        "chapter_writer": StepDef(
            tool="chapter_writer",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                **char_args,
            },
            depends_on=["search_outline"],
            retry_policy="auto",
            label="生成章节内容",
        ),
        "create_chapter": StepDef(
            tool="create_chapter",
            args={
                "outline_node_id": outline_node_id,
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "title": "{search_outline.data.0.title}",
            },
            depends_on=["chapter_writer"],
            label="保存章节",
        ),
        "detect_character_changes": StepDef(
            tool="detect_character_changes",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "chapter_id": "{create_chapter.data.chapter_id}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测角色变化",
        ),
        "detect_new_worldbuilding": StepDef(
            tool="detect_new_worldbuilding",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测新世界观元素",
        ),
    }

    return PlanGraph(name="fast_chapter", steps=steps)


def plan_quality_chapter(
    *,
    outline_node_id: str,
    requirements: str = "",
    involved_characters: list[str] | None = None,
) -> PlanGraph:
    """Quality chapter write path: preview -> plot -> roleplay -> write -> evaluate -> save -> detect."""
    chars = involved_characters or []
    char_args = {"involved_characters": chars} if chars else {}

    # Roleplay tool selection: 2+ characters -> dialogue_battle, else roleplay_character
    if len(chars) >= 2:
        roleplay_key = "dialogue_battle"
        roleplay_step = StepDef(
            tool="dialogue_battle",
            args={
                "character_names": chars,
                "scene": "{design_plot.data.scenes.0}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["design_plot"],
            label="多角色对话演练",
        )
    else:
        roleplay_key = "roleplay"
        roleplay_step = StepDef(
            tool="roleplay_character",
            args={
                "character_name": chars[0] if chars else "",
                "situation": "{design_plot.data.scenes.0}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["design_plot"],
            label="角色反应演练",
        )

    steps: dict[str, StepDef] = {
        "preview_context": StepDef(
            tool="preview_writing_context",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                **char_args,
            },
            depends_on=[],
            label="预览写作上下文",
        ),
        "design_plot": StepDef(
            tool="design_plot",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                "context_summary": "{preview_context.data}",
            },
            depends_on=["preview_context"],
            retry_policy="auto",
            label="设计剧情",
        ),
        roleplay_key: roleplay_step,
        "chapter_writer": StepDef(
            tool="chapter_writer",
            args={
                "outline_node_id": outline_node_id,
                "requirements": requirements,
                "previous_plot": "{design_plot.data}",
                "previous_roleplay": "{" + roleplay_key + ".data}",
                **char_args,
            },
            depends_on=[roleplay_key],
            retry_policy="auto",
            label="生成章节内容",
        ),
        "evaluate_chapter": StepDef(
            tool="evaluate_chapter",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["chapter_writer"],
            label="评估章节质量",
        ),
        "create_chapter": StepDef(
            tool="create_chapter",
            args={
                "outline_node_id": outline_node_id,
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "title": "{preview_context.data.outline_context.title}",
            },
            depends_on=["evaluate_chapter"],
            label="保存章节",
        ),
        "detect_character_changes": StepDef(
            tool="detect_character_changes",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "chapter_id": "{create_chapter.data.chapter_id}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测角色变化",
        ),
        "detect_new_worldbuilding": StepDef(
            tool="detect_new_worldbuilding",
            args={
                "draft_id": "{chapter_writer.data.draft_id}",
                "content_ref": "{chapter_writer.data.content_ref}",
                "outline_node_id": outline_node_id,
            },
            depends_on=["create_chapter"],
            label="检测新世界观元素",
        ),
    }

    return PlanGraph(name="quality_chapter", steps=steps)


def plan_cataloging_init(
    *,
    chapter_ids: list[str] | None = None,
) -> PlanGraph:
    """Cataloging initialization path.

    Delegates to the cataloging service via the plan orchestrator.
    """
    steps: dict[str, StepDef] = {
        "extract_facts": StepDef(
            tool="extract_facts",
            args={"chapter_ids": chapter_ids or []},
            depends_on=[],
            label="提取事实",
        ),
        "resolve_targets": StepDef(
            tool="resolve_targets",
            args={},
            depends_on=["extract_facts"],
            label="解析目标",
        ),
        "apply_candidates": StepDef(
            tool="apply_candidates",
            args={},
            depends_on=["resolve_targets"],
            label="应用候选",
        ),
    }

    return PlanGraph(name="cataloging_init", steps=steps)


def plan_cataloging_init(
    *,
    chapter_ids: list[str] | None = None,
    execution_mode: str = "auto",
) -> PlanGraph:
    """Cataloging initialization path backed by real workspace tools."""
    steps: dict[str, StepDef] = {
        "list_chapters": StepDef(
            tool="list_chapters",
            args={},
            depends_on=[],
            label="检查已有章节",
        ),
        "start_cataloging_job": StepDef(
            tool="start_cataloging_job",
            args={
                "chapter_ids": chapter_ids or [],
                "execution_mode": execution_mode if execution_mode in {"auto", "manual"} else "auto",
                "run_now": True,
            },
            depends_on=["list_chapters"],
            label="启动作品建档",
        ),
    }

    return PlanGraph(name="cataloging_init", steps=steps)


def plan_start_deconstruct(
    *,
    chapter_ids: list[str] | None = None,
    title: str = "",
) -> PlanGraph:
    """Legacy deconstruct analysis path."""
    steps: dict[str, StepDef] = {
        "preview_deconstruct_source": StepDef(
            tool="preview_deconstruct_source",
            args={},
            depends_on=[],
            label="检查可拆书内容",
        ),
        "start_deconstruct_job": StepDef(
            tool="start_deconstruct_job",
            args={
                "chapter_ids": chapter_ids or [],
                "title": title or "拆书分析",
                "analysis_mode": "fast",
                "include_golden_three": False,
                "run_now": True,
            },
            depends_on=["preview_deconstruct_source"],
            label="启动拆书分析",
        ),
    }
    return PlanGraph(name="start_deconstruct", steps=steps)


# ---------------------------------------------------------------------------
# Plan generators: character and worldbuilding
# ---------------------------------------------------------------------------

def plan_create_character(
    *,
    character_name: str = "",
    requirements: str = "",
) -> PlanGraph:
    """Character creation path: generate -> save."""
    steps: dict[str, StepDef] = {
        "character_writer": StepDef(
            tool="character_writer",
            args={
                "name": character_name,
                "requirements": requirements,
            },
            depends_on=[],
            retry_policy="auto",
            label="生成角色档案",
        ),
        "create_character": StepDef(
            tool="create_character",
            args={
                "name": "{character_writer.data.character.name}",
                "appearance": "{character_writer.data.character.appearance}",
                "personality": "{character_writer.data.character.personality}",
                "background": "{character_writer.data.character.background}",
                "abilities": "{character_writer.data.character.abilities}",
                "role_type": "{character_writer.data.character.role_type}",
                "speech_style": "{character_writer.data.character.speech_style}",
                "motivation": "{character_writer.data.character.motivation}",
                "conflict": "{character_writer.data.character.conflict}",
                "custom_system_prompt": "{character_writer.data.character.custom_system_prompt}",
            },
            depends_on=["character_writer"],
            label="保存角色",
        ),
    }
    return PlanGraph(name="create_character", steps=steps)


def plan_create_worldbuilding(
    *,
    topic: str = "",
    requirements: str = "",
) -> PlanGraph:
    """Worldbuilding creation path: generate -> save."""
    steps: dict[str, StepDef] = {
        "worldbuilding_writer": StepDef(
            tool="worldbuilding_writer",
            args={
                "title": topic,
                "requirements": requirements,
            },
            depends_on=[],
            retry_policy="auto",
            label="生成世界观设定",
        ),
        "create_worldbuilding_entry": StepDef(
            tool="create_worldbuilding_entry",
            args={
                "title": "{worldbuilding_writer.data.entry.title}",
                "content": "{worldbuilding_writer.data.entry.content}",
                "dimension": "{worldbuilding_writer.data.entry.dimension}",
                "plot_usage": "{worldbuilding_writer.data.entry.plot_usage}",
                "constraints": "{worldbuilding_writer.data.entry.constraints}",
            },
            depends_on=["worldbuilding_writer"],
            label="保存世界观条目",
        ),
    }
    return PlanGraph(name="create_worldbuilding", steps=steps)


def plan_create_scheduled_task(
    *,
    name: str,
    prompt: str,
    cron_expr: str | None = None,
    interval_minutes: int | None = None,
) -> PlanGraph:
    """Scheduled task creation path."""
    args: dict[str, Any] = {"name": name, "prompt": prompt}
    if cron_expr:
        args["cron_expr"] = cron_expr
    if interval_minutes:
        args["interval_minutes"] = interval_minutes
    steps = {
        "list_scheduled_tasks": StepDef(
            tool="list_scheduled_tasks",
            args={},
            depends_on=[],
            label="检查已有自动任务",
        ),
        "create_scheduled_task": StepDef(
            tool="create_scheduled_task",
            args=args,
            depends_on=["list_scheduled_tasks"],
            label="创建自动任务",
        ),
    }
    return PlanGraph(name="create_scheduled_task", steps=steps)


def plan_create_skill(
    *,
    requirements: str,
    scope: str = "global",
) -> PlanGraph:
    """Skill creation path."""
    steps = {
        "list_skills": StepDef(
            tool="list_skills",
            args={},
            depends_on=[],
            label="检查已有技能",
        ),
        "create_skill": StepDef(
            tool="create_skill",
            args={"requirements": requirements, "scope": scope},
            depends_on=["list_skills"],
            label="创建技能",
        ),
    }
    return PlanGraph(name="create_skill", steps=steps)


def plan_create_project(
    *,
    title: str,
    requirements: str = "",
) -> PlanGraph:
    """Project creation path."""
    steps = {
        "list_projects": StepDef(
            tool="list_projects",
            args={"query": title} if title else {},
            depends_on=[],
            label="检查已有作品",
        ),
        "create_project": StepDef(
            tool="create_project",
            args={"title": title or "未命名作品", "description": requirements},
            depends_on=["list_projects"],
            label="创建作品",
        ),
    }
    return PlanGraph(name="create_project", steps=steps)


def plan_create_novel(
    *,
    requirements: str = "",
) -> PlanGraph:
    """New novel creation path — start session, draft blueprints, review, apply."""
    steps = {
        "start_session": StepDef(
            tool="start_novel_creation_session",
            args={"user_brief": requirements, "mode": "internal_llm"},
            depends_on=[],
            label="创建新小说会话",
        ),
        "draft_blueprints": StepDef(
            tool="draft_novel_blueprint",
            args={"session_id": "$start_session.session_id", "execution_mode": "internal_llm"},
            depends_on=["start_session"],
            label="生成创意方案",
        ),
        "review_blueprint": StepDef(
            tool="review_novel_blueprint",
            args={"session_id": "$start_session.session_id", "execution_mode": "internal_llm"},
            depends_on=["draft_blueprints"],
            label="评审创意方案",
        ),
        "apply_blueprint": StepDef(
            tool="apply_novel_blueprint",
            args={"session_id": "$start_session.session_id", "mode": "auto"},
            depends_on=["review_blueprint"],
            label="应用蓝图创建项目",
        ),
    }
    return PlanGraph(name="create_novel", steps=steps)


def plan_external_writing(
    *,
    requirements: str = "",
) -> PlanGraph:
    """External writing path — prepare context, wait for external draft, save, review, apply."""
    steps = {
        "prepare_context": StepDef(
            tool="prepare_external_writing_context",
            args={"mode": "quality"},
            depends_on=[],
            label="准备写作上下文",
        ),
        "save_draft": StepDef(
            tool="save_external_chapter_draft",
            args={"content": "$external_draft", "source_agent": "external"},
            depends_on=["prepare_context"],
            label="保存外部草稿",
        ),
        "record_review": StepDef(
            tool="record_external_quality_review",
            args={"draft_id": "$save_draft.draft_id", "pass": True},
            depends_on=["save_draft"],
            label="记录质量评审",
        ),
        "create_chapter": StepDef(
            tool="create_chapter",
            args={"draft_id": "$save_draft.draft_id"},
            depends_on=["record_review"],
            label="创建章节",
        ),
        "apply_updates": StepDef(
            tool="apply_external_story_updates",
            args={"mode": "auto"},
            depends_on=["create_chapter"],
            label="应用故事更新",
        ),
    }
    return PlanGraph(name="external_writing", steps=steps)


def plan_export_project(
    *,
    scope: str = "all",
    fmt: str = "txt",
) -> PlanGraph:
    """Project export path."""
    steps = {
        "get_export_word_count": StepDef(
            tool="get_export_word_count",
            args={},
            depends_on=[],
            label="统计导出内容",
        ),
        "export_project": StepDef(
            tool="export_project",
            args={"scope": scope, "format": fmt},
            depends_on=["get_export_word_count"],
            label="生成导出文件",
        ),
    }
    return PlanGraph(name="export_project", steps=steps)


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_CHAPTER_RE = re.compile(r"第\s*(\d+)\s*章")
_QUALITY_KEYWORDS = {"精写", "高质量", "仔细写", "认真写", "质量", "quality"}

# Character creation keywords
_CHAR_KEYWORDS = ("创建角色", "新建角色", "新角色", "添加角色", "写角色", "生成角色", "设计角色")
# Worldbuilding keywords
_WB_KEYWORDS = ("创建世界观", "新建世界观", "世界观设定", "创建设定", "添加设定", "写世界观", "生成世界观", "世界观条目")
# Project init keywords (including cataloging/建档)
_INIT_KEYWORDS = ("初始化项目", "项目初始化", "自动规划", "帮我规划", "全面规划", "建档", "给项目建档", "给这个项目建档", "整理资料", "提取事实")
_SCHEDULE_KEYWORDS = ("定时", "自动任务", "计划任务", "每天", "每小时", "每周", "提醒我", "定期", "监控", "自动搜索")
_SKILL_KEYWORDS = ("创建技能", "新建技能", "添加技能", "写一个技能", "以后写作时", "以后生成时", "添加一种写法", "添加规则")
_PROJECT_KEYWORDS = ("创建作品", "新建作品", "添加作品", "开新书", "新建小说", "创建小说项目")
_EXPORT_KEYWORDS = ("导出", "导出作品", "导出章节", "导出全文", "生成导出")


def _extract_character_name(text: str) -> str:
    """Try to extract a character name from the user message."""
    # Patterns like "创建角色小明", "新角色：张三", "创建一个叫小明的角色"
    for pattern in [
        r"(?:创建|新建|添加|写|生成|设计)角色[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:叫|名为|名叫)\s*(.+?)(?:[，,。\s的]|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            name = m.group(1).strip()
            if 1 <= len(name) <= 20:
                return name
    return ""


def _extract_worldbuilding_topic(text: str) -> str:
    """Try to extract a worldbuilding topic from the user message."""
    for pattern in [
        r"(?:创建|新建|添加|写|生成)世界观[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:创建|新建|添加)设定[：:]?\s*(.+?)(?:[，,。\s]|$)",
        r"(?:关于)\s*(.+?)(?:的设定|的世界观|[，,。\s]|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            topic = m.group(1).strip()
            if 1 <= len(topic) <= 30:
                return topic
    return ""


def _extract_project_title(text: str) -> str:
    for pattern in [
        r"(?:创建|新建|添加)(?:作品|小说项目|小说)[：:]?\s*(.+?)(?:[，,。]|$)",
        r"开新书[：:]?\s*(.+?)(?:[，,。]|$)",
        r"(?:名为|叫)\s*(.+?)(?:的作品|的小说|[，,。]|$)",
    ]:
        m = re.search(pattern, text)
        if m:
            title = m.group(1).strip()
            if 1 <= len(title) <= 80:
                return title
    return ""


def _extract_schedule(text: str) -> tuple[str | None, int | None]:
    """Extract a simple cron or interval from Chinese schedule text."""
    m = re.search(r"每\s*(\d+)\s*分钟", text)
    if m:
        return None, max(1, int(m.group(1)))
    m = re.search(r"每\s*(\d+)\s*小时", text)
    if m:
        return None, max(1, int(m.group(1)) * 60)
    if "每小时" in text:
        return None, 60
    m = re.search(r"每天\s*(?:晚上|早上|上午|下午)?\s*(\d{1,2})\s*点", text)
    if m:
        hour = max(0, min(23, int(m.group(1))))
        return f"0 {hour} * * *", None
    if "每天" in text:
        return "0 9 * * *", None
    return None, None


def _task_name_from_message(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text)
    cleaned = re.sub(r"(帮我|请|创建|新建|添加|一个|自动任务|定时任务|定时|每天|每小时|每周|提醒我)", "", cleaned)
    return ("自动任务：" + cleaned[:20]) if cleaned else "自动任务"


def _export_format(text: str) -> str:
    lowered = text.lower()
    if "docx" in lowered or "word" in lowered:
        return "docx"
    if "pdf" in lowered:
        return "pdf"
    return "txt"


def _export_scope(text: str) -> str:
    if "大纲" in text:
        return "outline"
    if "角色" in text:
        return "characters"
    if "世界观" in text or "设定" in text:
        return "worldbuilding"
    if "章节" in text or "正文" in text or "全文" in text:
        return "chapters"
    return "all"


def detect_intent(user_message: str) -> dict[str, Any] | None:
    """Parse user intent from a natural language message.

    Returns dict with keys:
      - intent_type: "chapter" | "character" | "worldbuilding" | "project_init" | "scheduled_task" | "skill" | "project" | "export"
      - For chapter: mode, outline_query, chapter_number
      - For character: character_name, requirements
      - For worldbuilding: topic, requirements
      - For project_init: requirements

    Returns None if no recognizable intent detected.
    """
    text = user_message.strip()
    if not text:
        return None

    # Legacy deconstruct / reading-analysis job.
    if any(kw in text for kw in ("拆书", "拆书分析", "阅读分析", "分析全书", "分析作品")):
        return {"intent_type": _INTENT_DECONSTRUCT, "requirements": text}

    # 1. Project init
    if any(kw in text for kw in _INIT_KEYWORDS):
        return {"intent_type": _INTENT_PROJECT_INIT, "requirements": text}

    # 1.1 Create novel (new novel from scratch)
    _CREATE_NOVEL_KEYWORDS = [
        "开一本", "开书", "创建新小说", "新小说", "写一本",
        "帮我开", "帮我写一本", "从零开始写", "搭建一本",
    ]
    if any(kw in text for kw in _CREATE_NOVEL_KEYWORDS):
        return {"intent_type": _INTENT_CREATE_NOVEL, "requirements": text}

    # 1.2 External writing (Claude Code / Codex writes)
    _EXTERNAL_WRITING_KEYWORDS = [
        "外部写作", "外部写", "让claude写", "让codex写",
        "外部agent写", "外部模型写", "不用内部api写",
    ]
    if any(kw in text for kw in _EXTERNAL_WRITING_KEYWORDS):
        return {"intent_type": _INTENT_EXTERNAL_WRITING, "requirements": text}

    # 1.5 Scheduled tasks
    if any(kw in text for kw in _SCHEDULE_KEYWORDS) and any(kw in text for kw in ("任务", "搜索", "整理", "提醒", "监控", "收集")):
        cron_expr, interval_minutes = _extract_schedule(text)
        return {
            "intent_type": _INTENT_SCHEDULED_TASK,
            "name": _task_name_from_message(text),
            "prompt": text,
            "cron_expr": cron_expr,
            "interval_minutes": interval_minutes,
        }

    # 1.6 Skill creation
    if any(kw in text for kw in _SKILL_KEYWORDS):
        return {"intent_type": _INTENT_SKILL, "requirements": text, "scope": "global"}

    # 1.7 Project creation
    if any(kw in text for kw in _PROJECT_KEYWORDS):
        return {"intent_type": _INTENT_PROJECT, "title": _extract_project_title(text), "requirements": text}

    # 1.8 Export
    if any(kw in text for kw in _EXPORT_KEYWORDS):
        return {"intent_type": _INTENT_EXPORT, "scope": _export_scope(text), "format": _export_format(text)}

    # 2. Character creation
    if any(kw in text for kw in _CHAR_KEYWORDS):
        return {
            "intent_type": _INTENT_CHARACTER,
            "character_name": _extract_character_name(text),
            "requirements": text,
        }

    # 3. Worldbuilding creation
    if any(kw in text for kw in _WB_KEYWORDS):
        return {
            "intent_type": _INTENT_WORLDBUILDING,
            "topic": _extract_worldbuilding_topic(text),
            "requirements": text,
        }

    # 4. Chapter writing
    chapter_match = _CHAPTER_RE.search(text)
    if chapter_match:
        chapter_number = int(chapter_match.group(1))
        mode = "quality" if any(kw in text for kw in _QUALITY_KEYWORDS) else "fast"
        return {
            "intent_type": _INTENT_CHAPTER,
            "mode": mode,
            "outline_query": text,
            "requirements": text,
            "chapter_number": chapter_number,
        }

    if any(kw in text for kw in ("写章", "写一章", "续写", "继续写")):
        mode = "quality" if any(kw in text for kw in _QUALITY_KEYWORDS) else "fast"
        return {
            "intent_type": _INTENT_CHAPTER,
            "mode": mode,
            "outline_query": text,
            "requirements": text,
            "chapter_number": None,
        }

    return None


def build_plan_from_intent(intent: dict[str, Any], *, outline_node_id: str = "") -> PlanGraph | None:
    """Build a PlanGraph from a detected intent dict.

    Returns None if the intent type is not supported for plan execution.
    """
    intent_type = intent.get("intent_type")

    if intent_type == _INTENT_CHAPTER:
        mode = intent.get("mode", "fast")
        if mode == "quality":
            return plan_quality_chapter(
                outline_node_id=outline_node_id,
                requirements=intent.get("requirements", ""),
            )
        return plan_fast_chapter(
            outline_node_id=outline_node_id,
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_CHARACTER:
        return plan_create_character(
            character_name=intent.get("character_name", ""),
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_WORLDBUILDING:
        return plan_create_worldbuilding(
            topic=intent.get("topic", ""),
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_SCHEDULED_TASK:
        return plan_create_scheduled_task(
            name=intent.get("name", "自动任务"),
            prompt=intent.get("prompt", ""),
            cron_expr=intent.get("cron_expr"),
            interval_minutes=intent.get("interval_minutes"),
        )

    if intent_type == _INTENT_CREATE_NOVEL:
        return plan_create_novel(
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_EXTERNAL_WRITING:
        return plan_external_writing(
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_SKILL:
        return plan_create_skill(
            requirements=intent.get("requirements", ""),
            scope=intent.get("scope", "global"),
        )

    if intent_type == _INTENT_PROJECT:
        return plan_create_project(
            title=intent.get("title", ""),
            requirements=intent.get("requirements", ""),
        )

    if intent_type == _INTENT_EXPORT:
        return plan_export_project(
            scope=intent.get("scope", "all"),
            fmt=intent.get("format", "txt"),
        )

    # project_init: not yet implemented as a plan — fall back to old assistant
    if intent_type == _INTENT_DECONSTRUCT:
        return plan_start_deconstruct(title=intent.get("requirements", "")[:80])

    if intent_type == _INTENT_PROJECT_INIT:
        return plan_cataloging_init(execution_mode="auto")

    return None
