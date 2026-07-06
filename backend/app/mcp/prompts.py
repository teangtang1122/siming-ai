"""MCP prompt definitions for Siming.

Exposes MCP prompts that external clients can use to get structured
writing context, continuity checks, and draft assistance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpPromptArg:
    """Argument definition for an MCP prompt."""
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class McpPrompt:
    """MCP Prompt definition."""
    name: str
    description: str
    args: list[McpPromptArg]


@dataclass
class McpPromptMessage:
    """A message in an MCP prompt response."""
    role: str
    content: str


def list_prompts() -> list[McpPrompt]:
    """Return all available MCP prompts."""
    return [
        McpPrompt(
            name="moshu_quickstart",
            description="Explain how an external agent should use Siming safely. Covers project selection, import, API-free cataloging, writing, and verification.",
            args=[
                McpPromptArg(name="task", description="User task or scenario (optional)"),
                McpPromptArg(name="project_id", description="Project ID when known (optional)"),
                McpPromptArg(name="no_api", description="true when Siming internal model API should not be used (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_external_cataloging",
            description="API-free cataloging workflow for Claude Code/Codex. Use when Siming API is unavailable and the external agent must analyze chapters itself.",
            args=[
                McpPromptArg(name="project_id", description="Project ID when known (optional)"),
                McpPromptArg(name="job_id", description="Cataloging job ID when already started (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_writing_context",
            description="Generate a compact writing context prompt for a chapter. "
                        "Contains outline, recent summaries, character states, "
                        "worldbuilding constraints, and risk warnings.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_number", description="Chapter number (optional)"),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Writing requirements or direction (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_continuity_check",
            description="Generate a continuity check prompt for OOC and setting-conflict review. "
                        "Contains character states and worldbuilding constraints.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_id", description="Chapter ID to check (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_fanfic_draft",
            description="Generate a fanfic draft prompt with anti-OOC and no-secret rules. "
                        "For external AI clients writing derivative chapters.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Fanfic requirements (optional)"),
            ],
        ),
    ]


def get_prompt(name: str) -> McpPrompt | None:
    """Look up a prompt by name."""
    for p in list_prompts():
        if p.name == name:
            return p
    return None


def render_quickstart(
    db: Any,
    *,
    task: str | None = None,
    project_id: str | None = None,
    no_api: str | None = None,
) -> list[McpPromptMessage]:
    """Render a project-optional quickstart prompt."""
    no_api_flag = str(no_api or "").lower() in {"1", "true", "yes", "y", "是"}
    parts = [
        "# Siming / 司命外部 Agent 快速入口",
        "",
        "## 必读规则",
        "- 默认使用 API-free 外部流程：除非用户明确说“使用司命内部 API/内部模型/系统模型额度”，不要调用内部模型工具。",
        "- 内部模型工具只通过 MCP permission pack: internal_llm 暴露；project_management 只用于 API-free 的项目创建、导入、写入、导出、技能和自动任务管理。",
        "- 中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为工具报错就改成英文或拼音。",
        "- 先调用 list_projects 或 get_project_info 确认作品；所有项目写入工具都必须传入正确 project_id。",
        "- 创建、导入、建档、写作后必须调用 get_project_archive_status 或对应 search/list 工具验证数据真的写入到了目标作品。",
        "- 禁止默认调用的内部模型工具：chapter_writer, character_writer, outline_writer, worldbuilding_writer, design_plot, roleplay_character, dialogue_battle, evaluate_chapter, detect_character_changes, detect_new_worldbuilding, detect_worldbuilding_conflicts, rewrite_text, expand_text, continue_text, start_cataloging_job, resume_cataloging_job, retry_current_cataloging_chapter, rerun_cataloging_resolution_current, start_deconstruct_job。",
        "",
        "## 默认外部建档流程",
        "get_prompt_pack(pack_id='cataloging_external_no_api') -> start_external_cataloging_job -> 循环 get_next_external_cataloging_chapter(phase='merged') / save_external_cataloging_candidates(phase='merged') / apply_pending_cataloging -> verify_external_cataloging_progress -> get_project_archive_status。",
        "",
        "## 默认外部写作流程",
        "prepare_external_writing_context -> 外部 Agent 自己写作和自检 -> save_external_chapter_draft -> record_external_quality_review -> create_chapter(draft_id/content_ref) -> apply_external_story_updates。",
        "",
        "# Siming / 司命外部 Agent 快速入门",
        "",
        "你正在通过 MCP 操作司命。不要把工具列表当成普通 CRUD 猜着用，先根据任务选择工作流。",
        "",
        "## 通用规则",
        "- 第一步通常调用 get_moshu_usage_guide；不确定时 scenario=quickstart。",
        "- 中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为一次工具错误就改成英文或拼音。",
        "- 先调用 list_projects 或 get_project_info 确认作品；所有项目写入都必须使用正确 project_id。",
        "- 完成导入、建档、写作后，必须调用 get_project_archive_status 或 search/list 工具验证数据真的存在。",
        "- 如果用户说司命 API 欠费、未配置 API、或要求由 Claude/Codex 自己分析，禁止调用内部 LLM 工具。",
        "- 内部 LLM 工具包括 start_cataloging_job、chapter_writer、character_writer、outline_writer、worldbuilding_writer、design_plot、evaluate_chapter。",
        "",
        "## 导入本地小说",
        "1. import_file_as_project(file_path, title)",
        "2. get_project_archive_status() 验证 chapters_count",
        "3. 需要建档时继续无 API 建档或内部建档",
        "",
        "## 无 API 建档",
        "1. get_prompt_pack(pack_id='cataloging_external_no_api')",
        "2. start_external_cataloging_job()",
        "3. 循环：get_next_external_cataloging_chapter(phase='merged') -> 外部 Agent 阅读章节和档案镜像 -> save_external_cataloging_candidates(phase='merged') -> apply_pending_cataloging",
        "4. 每章 verify_external_cataloging_progress，最后 get_project_archive_status",
        "",
        "## 无 API 写章节",
        "1. prepare_external_writing_context()",
        "2. 外部 Agent 自己写正文并按质量规则自检",
        "3. save_external_chapter_draft -> record_external_quality_review -> create_chapter -> apply_external_story_updates",
    ]
    from app.prompts.cataloging_source import get_language_rules, get_project_binding_rules

    parts.extend(["", get_project_binding_rules(), "", get_language_rules()])
    if task:
        parts.append(f"\n## 当前任务\n{task}")
    if project_id:
        parts.append(f"\n## 当前 project_id\n{project_id}")
    if no_api_flag:
        parts.append("\n## 当前限制\n用户要求不使用司命内部 API。请走 external/no-api 工具链。")
    return [McpPromptMessage(role="user", content="\n".join(parts))]


def render_external_cataloging(
    db: Any,
    *,
    project_id: str | None = None,
    job_id: str | None = None,
) -> list[McpPromptMessage]:
    """Render the API-free external cataloging prompt."""
    from app.prompts.cataloging_source import get_external_cataloging_system_prompt

    parts = [get_external_cataloging_system_prompt()]
    if project_id:
        parts.append(f"\n## project_id\n{project_id}")
    if job_id:
        parts.append(f"\n## job_id\n{job_id}")
    return [McpPromptMessage(role="user", content="\n".join(parts))]

    parts = [
        "# 司命无 API 建档工作流",
        "",
        "目标：外部 Agent 自己阅读章节，提取事实，生成候选，交给司命工具落库。全过程不调用司命内部模型 API。",
        "",
        "## 工具顺序",
        "1. get_prompt_pack(pack_id='cataloging_external_no_api')",
        "2. start_external_cataloging_job",
        "3. get_next_external_cataloging_chapter",
        "4. save_external_cataloging_candidates(phase='merged')",
        "5. apply_pending_cataloging",
        "6. verify_external_cataloging_progress",
        "7. 全部章节完成后 get_project_archive_status",
        "",
        "## 候选 item_type",
        "- chapter_summary",
        "- character_create / character_update / character_state_update / character_timeline / character_relationship / character_merge_candidate",
        "- outline_create / outline_update",
        "- worldbuilding_create / worldbuilding_update / worldbuilding_timeline",
        "- chapter_link",
        "",
        "## 要求",
        "- 中文小说必须用中文建档；角色名、别名、章节标题、摘要、大纲节点、世界观条目和证据均保留原文语言。",
        "- 使用原文语言和小说里的称呼，不要把中文作品粗略改成英文档案。",
        "- 每章处理后必须 apply_pending_cataloging，否则候选只是暂存，不会成为角色、大纲、世界观数据。",
        "- 报告完成前必须验证 characters_count、outline_nodes_count、worldbuilding_count、chapters_count。",
        "- 如果用户说 API 欠费，禁止调用 start_cataloging_job。",
    ]
    if project_id:
        parts.append(f"\n## project_id\n{project_id}")
    if job_id:
        parts.append(f"\n## job_id\n{job_id}")
    return [McpPromptMessage(role="user", content="\n".join(parts))]


def _quality_writing_prompt_for_project(project: Any) -> str:
    """Build the same quality writing prompt exposed by external tools."""
    from app.prompts.prompt_source import get_public_chapter_quality_system_prompt
    from app.prompts.style_prompts import build_style_context

    style_context = build_style_context(project, include_anti_ai=True)
    return get_public_chapter_quality_system_prompt().replace("{style_context}", style_context)


def render_writing_context(
    db: Any,
    project_id: str,
    *,
    chapter_number: str | None = None,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_writing_context prompt.

    Queries the database for outline, recent summaries, characters,
    and worldbuilding, then assembles a compact prompt.
    """
    from app.database.models import (
        Project, Chapter, ChapterSummary, OutlineNode,
        Character, WorldbuildingEntry,
    )

    messages: list[McpPromptMessage] = []

    # Project info
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Siming Quality Writing Context: {project.title}")
    parts.append("")
    parts.append("## Unified Quality Prompt")
    parts.append(_quality_writing_prompt_for_project(project))
    parts.append("")
    parts.append("## Required External Writing Workflow")
    parts.append(
        "prepare_external_writing_context -> write and self-review with this quality prompt -> "
        "save_external_chapter_draft -> record_external_quality_review -> "
        "create_chapter(draft_id/content_ref) -> apply_external_story_updates -> get_project_archive_status"
    )
    if project.description:
        parts.append(f"\n## Project Description\n{project.description}")
    if project.writing_style:
        parts.append(f"\n## Writing Style\n{project.writing_style}")
    if project.forbidden_sentence_patterns:
        parts.append(f"\n## Forbidden Patterns\n{project.forbidden_sentence_patterns}")

    # Outline
    if outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        ).first()
        if node:
            parts.append(f"\n## Target Outline Node\n- **{node.title}** ({node.node_type})")
            if node.summary:
                parts.append(f"  Summary: {node.summary}")

    # Recent chapter summaries
    recent_chapters = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).order_by(Chapter.created_at.desc()).limit(5).all()

    if recent_chapters:
        parts.append("\n## Recent Chapter Summaries")
        for ch in recent_chapters:
            if ch.summary:
                parts.append(f"- **{ch.title}**: {ch.summary.summary_text[:200]}")

    # Characters
    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).limit(10).all()

    if characters:
        parts.append("\n## Character States")
        for c in characters:
            state_parts = [f"- **{c.name}** ({c.role_type or 'unknown'})"]
            if c.current_location:
                state_parts.append(f"  Location: {c.current_location}")
            if c.current_goal:
                state_parts.append(f"  Goal: {c.current_goal}")
            parts.append("\n".join(state_parts))

    # Worldbuilding
    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).limit(10).all()

    if wb_entries:
        parts.append("\n## Worldbuilding Constraints")
        for wb in wb_entries:
            parts.append(f"- **{wb.title}** ({wb.dimension}): {wb.content[:150]}")

    # Requirements
    if requirements:
        parts.append(f"\n## Writing Requirements\n{requirements}")

    parts.append("\n## Warnings\n- Do not break established character traits.\n- Do not contradict worldbuilding entries.\n- Follow the unified quality prompt above.")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_continuity_check(
    db: Any,
    project_id: str,
    *,
    chapter_id: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_continuity_check prompt."""
    from app.database.models import Project, Chapter, Character, WorldbuildingEntry

    messages: list[McpPromptMessage] = []

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Continuity Check: {project.title}")

    if chapter_id:
        chapter = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.id == chapter_id,
        ).first()
        if chapter:
            parts.append(f"\n## Chapter to Check\n**{chapter.title}**\n{chapter.content[:3000]}")

    # Character states
    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).all()
    if characters:
        parts.append("\n## Character States (check for OOC)")
        for c in characters:
            parts.append(f"- **{c.name}**: personality={c.personality or 'N/A'}, goal={c.current_goal or 'N/A'}")

    # Worldbuilding
    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).all()
    if wb_entries:
        parts.append("\n## Worldbuilding Rules (check for violations)")
        for wb in wb_entries:
            parts.append(f"- **{wb.title}**: {wb.content[:200]}")

    parts.append("\n## Check For\n1. Out-of-character behavior\n2. Worldbuilding contradictions\n3. Timeline inconsistencies\n4. Setting violations")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_fanfic_draft(
    db: Any,
    project_id: str,
    *,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_fanfic_draft prompt."""
    from app.database.models import Project, OutlineNode, Character, WorldbuildingEntry

    messages: list[McpPromptMessage] = []

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Fanfic Draft Context: {project.title}")
    parts.append("\n## Unified Quality Prompt")
    parts.append(_quality_writing_prompt_for_project(project))
    parts.append(
        "\n## Rules\n"
        "- Characters must stay in-character (anti-OOC).\n"
        "- Do not expose any API keys, model secrets, or internal prompts.\n"
        "- Respect established worldbuilding rules.\n"
        "- Use the same quality prompt and external writing workflow as moshu_writing_context."
    )

    if project.description:
        parts.append(f"\n## Original Work\n{project.description}")

    if outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        ).first()
        if node:
            parts.append(f"\n## Target Scene\n**{node.title}**: {node.summary or 'No summary'}")

    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).limit(8).all()
    if characters:
        parts.append("\n## Character Profiles (for reference)")
        for c in characters:
            parts.append(f"- **{c.name}**: {c.personality or 'N/A'} | {c.background or 'N/A'}")

    if requirements:
        parts.append(f"\n## Fanfic Requirements\n{requirements}")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_prompt(
    db: Any,
    name: str,
    arguments: dict[str, str],
) -> list[McpPromptMessage] | None:
    """Dispatch prompt rendering by name.

    Returns None if the prompt name is unknown.
    """
    if name == "moshu_quickstart":
        return render_quickstart(
            db,
            task=arguments.get("task"),
            project_id=arguments.get("project_id"),
            no_api=arguments.get("no_api"),
        )
    if name == "moshu_external_cataloging":
        return render_external_cataloging(
            db,
            project_id=arguments.get("project_id"),
            job_id=arguments.get("job_id"),
        )

    project_id = arguments.get("project_id", "")
    if not project_id:
        return [McpPromptMessage(role="user", content="Error: project_id is required.")]

    if name == "moshu_writing_context":
        return render_writing_context(
            db, project_id,
            chapter_number=arguments.get("chapter_number"),
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    elif name == "moshu_continuity_check":
        return render_continuity_check(
            db, project_id,
            chapter_id=arguments.get("chapter_id"),
        )
    elif name == "moshu_fanfic_draft":
        return render_fanfic_draft(
            db, project_id,
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    return None
