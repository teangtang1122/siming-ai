"""Prompt pack tools — read public prompt packs and method cards.

These tools are API-free and exposed to internal assistant, scheduler,
and MCP readonly collaboration pack.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


async def get_moshu_usage_guide(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Return the external-agent quickstart and scenario workflows.

    This tool is intentionally API-free. It gives Claude Code, Codex, and the
    in-app assistant a deterministic first stop when they do not know which
    Moshu workflow to use.
    """
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scenario = str(args.get("scenario") or "quickstart").strip() or "quickstart"
    no_api = bool(args.get("no_api") if "no_api" in args else scenario in {"cataloging_no_api", "writing_no_api"})

    internal_llm_tools = [
        "chapter_writer",
        "character_writer",
        "outline_writer",
        "worldbuilding_writer",
        "design_plot",
        "roleplay_character",
        "dialogue_battle",
        "evaluate_chapter",
        "detect_character_changes",
        "detect_new_worldbuilding",
        "detect_worldbuilding_conflicts",
        "rewrite_text",
        "expand_text",
        "continue_text",
        "start_cataloging_job",
        "resume_cataloging_job",
        "retry_current_cataloging_chapter",
        "rerun_cataloging_resolution_current",
        "start_deconstruct_job",
    ]

    workflows = {
        "quickstart": {
            "title": "墨枢外部 Agent 快速入口",
            "rules": [
                "默认使用 API-free 外部流程：除非用户明确说“使用墨枢内部 API/内部模型/系统模型额度”，不要调用内部模型工具。",
                "内部模型工具现在只通过 MCP permission pack: internal_llm 暴露；project_management 只用于 API-free 的项目创建、导入、写入、导出、技能和自动任务管理。",
                "中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为工具报错就改成英文或拼音。",
                "先调用 list_projects 或 get_project_info 确认作品；所有项目写入工具都必须传入正确 project_id。",
                "创建、导入、建档、写作后必须调用 get_project_archive_status 或对应 search/list 工具验证数据真的写入到了目标作品。",
                "长正文、完整章节、完整档案和大量候选 JSON 不要完整输出到聊天里；应写入 save_external_chapter_draft、save_external_cataloging_facts、save_external_cataloging_candidates 或对应写入工具，聊天只返回摘要、ID、字数、数量和验证结果。",
            ],
            "first_tools": [
                "get_mcp_permission_status",
                "list_projects",
                "get_project_archive_status",
                "list_prompt_packs",
                "get_prompt_pack",
            ],
            "internal_llm_tools_forbidden_by_default": internal_llm_tools,
        },
        "import_file": {
            "title": "导入本地小说为新作品",
            "steps": [
                "调用 import_file_as_project(file_path, title)。",
                "读取返回的 project.id；之后所有写入都使用这个 project_id。",
                "调用 get_project_archive_status 验证 chapters_count 是否正确。",
                "如果用户还要建档，默认继续 cataloging_no_api；只有用户明确授权内部 API 时才走 cataloging_internal。",
            ],
        },
        "cataloging_no_api": {
            "title": "API-free 建档，由外部 Agent 自己读章节并写入",
            "steps": [
                "调用 get_prompt_pack(pack_id='cataloging_external_no_api') 读取建档提示词和输出契约。",
                "调用 start_external_cataloging_job 创建外部建档任务。",
                "循环：get_next_external_cataloging_chapter -> 外部 Agent 阅读章节 -> save_external_cataloging_facts -> save_external_cataloging_candidates -> apply_pending_cataloging。",
                "每章 apply 后调用 verify_external_cataloging_progress；发现 pending_candidates 或 warnings 时先处理，不要跳过关键章节。",
                "最终调用 get_project_archive_status，确认角色、大纲、世界观、章节摘要数量符合预期后再报告完成。",
            ],
            "forbidden_tools": internal_llm_tools,
        },
        "cataloging_internal": {
            "title": "使用墨枢内部 API 建档",
            "steps": [
                "只有用户明确授权使用墨枢内部 API/内部模型时才能进入此流程。",
                "确认 MCP 权限包为 internal_llm，且系统设置里的模型 API 可用。",
                "调用 start_cataloging_job；前端会显示实时进度。",
                "失败时使用 retry_current_cataloging_chapter 或 rerun_cataloging_resolution_current。",
                "完成后调用 get_project_archive_status 验证数据。",
            ],
        },
        "writing_no_api": {
            "title": "API-free 写作，由外部 Agent 生成正文",
            "steps": [
                "调用 prepare_external_writing_context 获取大纲、角色、世界观、摘要、质量规则和禁用句式。",
                "外部 Agent 按 prompt pack 自己写正文并自检。",
                "调用 save_external_chapter_draft 保存完整草稿；聊天里不要完整输出正文。",
                "调用 record_external_quality_review 记录外部质量检查。",
                "用户确认后调用 create_chapter，并传 draft_id/content_ref；随后调用 apply_external_story_updates 写入角色状态、章节摘要和世界观变化。",
            ],
            "forbidden_tools": internal_llm_tools,
        },
        "writing_internal": {
            "title": "使用墨枢内部 API 写作",
            "steps": [
                "只有用户明确授权使用墨枢内部 API/内部模型时才能进入此流程。",
                "确认 MCP 权限包为 internal_llm。",
                "质量模式会检索上下文、设计剧情、角色对戏、生成正文、评估、检测角色和世界观变化。",
                "内部写作会消耗系统设置里的模型 API 额度。",
            ],
        },
    }

    selected = workflows.get(scenario, workflows["quickstart"])
    return {
        "tool": "get_moshu_usage_guide",
        "status": "ok",
        "detail": f"Usage guide: {scenario}",
        "data": {
            "scenario": scenario,
            "project_id": project_id,
            "no_api": no_api,
            "default_mode": "api_free_external",
            "internal_llm_requires_explicit_user_opt_in": True,
            "guide": selected,
            "recommended_next": _recommended_next(scenario, no_api),
        },
    }

    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scenario = str(args.get("scenario") or "quickstart").strip() or "quickstart"
    no_api = bool(args.get("no_api") if "no_api" in args else scenario in {"cataloging_no_api", "writing_no_api"})

    workflows = {
        "quickstart": {
            "title": "墨枢外部 Agent 快速入口",
            "rules": [
                "中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为工具调用失败就改成英文或拼音，除非用户明确要求翻译。",
                "先调用 list_projects 或 get_project_info 确认作品；所有写入工具都必须传入正确 project_id。",
                "如果用户说 API 欠费、没有在墨枢配置 API、或要求由 Claude/Codex 自己分析，禁止调用 start_cataloging_job、chapter_writer、character_writer、outline_writer、worldbuilding_writer 这类内部 LLM 工具。",
                "创建或导入后不要只凭工具返回口头确认，必须调用 get_project_archive_status 或对应 search/list 工具验证数据真的存在。",
                "遇到不确定流程，先调用 get_prompt_pack(pack_id='cataloging_external_no_api') 或 get_tool_playbook，而不是手动猜 CRUD。",
                "长正文、完整章节、完整档案和大量候选 JSON 不要完整输出到聊天里；必须写入 save_external_chapter_draft、save_external_cataloging_facts、save_external_cataloging_candidates 或对应写入工具，聊天只返回摘要、ID、字数、数量和验证结果。",
            ],
            "first_tools": [
                "get_mcp_permission_status",
                "list_projects",
                "get_project_archive_status",
                "list_prompt_packs",
                "get_prompt_pack",
            ],
        },
        "import_file": {
            "title": "把本地 txt/docx 等导入为新作品",
            "steps": [
                "调用 import_file_as_project，传入 file_path 和 title。",
                "读取返回的 project.id；之后所有写入都使用这个 project_id。",
                "调用 get_project_archive_status 验证 chapters_count 是否正确。",
                "如果用户还要建档，按 cataloging_no_api 或 cataloging_internal 分支继续。",
            ],
        },
        "cataloging_no_api": {
            "title": "无墨枢 API 建档，由外部 Agent 自己读章节并写入",
            "steps": [
                "语言规则：中文小说全程用中文建档；角色名、别名、章节标题、摘要、大纲、世界观和证据都保留原文语言。",
                "调用 get_prompt_pack(pack_id='cataloging_external_no_api') 读取建档提示词和输出契约。",
                "调用 start_external_cataloging_job 创建任务。",
                "循环：get_next_external_cataloging_chapter -> 由外部 Agent 阅读章节 -> save_external_cataloging_facts -> save_external_cataloging_candidates -> apply_pending_cataloging。",
                "每章 apply 后调用 verify_external_cataloging_progress；发现 pending_candidates 或 warnings 时先处理，不要跳过关键章节。",
                "最终调用 get_project_archive_status，确认角色、大纲、世界观、章节摘要数量符合预期后才报告完成。",
            ],
            "canonical_candidate_types": [
                "chapter_summary",
                "character_create",
                "character_update",
                "character_state_update",
                "character_timeline",
                "character_relationship",
                "character_merge_candidate",
                "outline_create",
                "outline_update",
                "worldbuilding_create",
                "worldbuilding_update",
                "worldbuilding_timeline",
                "chapter_link",
            ],
            "forbidden_when_no_api": [
                "start_cataloging_job",
                "chapter_writer",
                "character_writer",
                "outline_writer",
                "worldbuilding_writer",
                "design_plot",
                "evaluate_chapter",
            ],
        },
        "cataloging_internal": {
            "title": "使用墨枢内部 API 建档",
            "steps": [
                "确认系统设置里 API 可用且用户允许消耗模型额度。",
                "调用 start_cataloging_job；前端会显示实时进度。",
                "失败时使用 retry_current_cataloging_chapter 或 rerun_cataloging_resolution_current。",
                "完成后调用 get_project_archive_status 验证数据。",
            ],
        },
        "writing_no_api": {
            "title": "无墨枢 API 写作，由外部 Agent 生成正文",
            "steps": [
                "调用 prepare_external_writing_context 获取大纲、角色、世界观、摘要、质量规则和禁用句式。",
                "外部 Agent 按 prompt pack 自己写正文并自检。",
                "调用 save_external_chapter_draft 保存完整草稿；聊天里不要完整输出正文。",
                "调用 record_external_quality_review 记录外部质量检查。",
                "用户确认后调用 create_chapter，并传 draft_id/content_ref，不要把整章正文塞进 content；再调用 apply_external_story_updates 写入角色状态、章节摘要、世界观变化。",
            ],
        },
        "writing_internal": {
            "title": "使用墨枢内部 API 写作",
            "steps": [
                "质量模式会检索上下文、设计剧情、角色对戏、生成正文、评估、检测角色和世界观变化。",
                "快速模式会减少评估和角色对戏，优先速度。",
                "内部写作会消耗系统设置里的模型 API 额度。",
            ],
        },
    }

    selected = workflows.get(scenario, workflows["quickstart"])
    return {
        "tool": "get_moshu_usage_guide",
        "status": "ok",
        "detail": f"Usage guide: {scenario}",
        "data": {
            "scenario": scenario,
            "project_id": project_id,
            "no_api": no_api,
            "guide": selected,
            "recommended_next": _recommended_next(scenario, no_api),
        },
    }


def _recommended_next(scenario: str, no_api: bool) -> list[dict[str, Any]]:
    if scenario == "cataloging_no_api" or no_api:
        return [
            {"tool": "get_prompt_pack", "arguments": {"pack_id": "cataloging_external_no_api"}},
            {"tool": "start_external_cataloging_job", "arguments": {}},
        ]
    if scenario == "import_file":
        return [{"tool": "import_file_as_project", "arguments": {"file_path": "<path>", "title": "<title>"}}]
    return [{"tool": "list_projects", "arguments": {}}, {"tool": "get_mcp_permission_status", "arguments": {}}]


async def list_prompt_packs(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """List available public prompt packs."""
    from app.database.models import PublicPromptPack
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scope = str(args.get("scope") or "").strip()
    query = db.query(PublicPromptPack).filter(PublicPromptPack.enabled == True)
    if scope:
        query = query.filter(PublicPromptPack.scope == scope)

    packs = query.order_by(PublicPromptPack.scope, PublicPromptPack.pack_id).all()

    return {
        "tool": "list_prompt_packs",
        "status": "ok",
        "detail": f"Found {len(packs)} prompt packs",
        "data": {
            "items": [
                {
                    "pack_id": p.pack_id,
                    "version": p.version,
                    "scope": p.scope,
                    "title": p.title,
                    "summary": p.summary,
                    "is_builtin": p.is_builtin,
                }
                for p in packs
            ],
            "total": len(packs),
        },
    }


async def get_prompt_pack(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Get a specific prompt pack by scope and mode."""
    from app.database.models import PublicPromptPack
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scope = str(args.get("scope") or "chapter_writing").strip()
    mode = str(args.get("mode") or "quality").strip()
    pack_id = str(args.get("pack_id") or "").strip()

    # Find by pack_id or by scope+mode
    if pack_id:
        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()
    else:
        # Map scope+mode to pack_id
        scope_mode_map = {
            ("chapter_writing", "quality"): "chapter_writing_quality",
            ("chapter_writing", "fast"): "chapter_writing_fast",
            ("chapter_review", "quality"): "chapter_review_quality",
            ("new_project", ""): "new_project_setup",
            ("character_design", ""): "character_design",
            ("worldbuilding", ""): "worldbuilding_design",
            ("outline_planning", ""): "outline_planning",
            ("cataloging", "external_no_api"): "cataloging_external_no_api",
            ("cataloging", ""): "cataloging_external_no_api",
            ("anti_ai_review", ""): "anti_ai_review",
            ("character_change_detection", ""): "character_change_detection",
            ("worldbuilding_detection", ""): "worldbuilding_detection",
            ("chapter_evaluation", ""): "chapter_evaluation",
            ("conflict_suggestion", ""): "conflict_suggestion",
        }
        mapped_id = scope_mode_map.get((scope, mode), scope_mode_map.get((scope, ""), ""))
        if mapped_id:
            pack = db.query(PublicPromptPack).filter(
                PublicPromptPack.pack_id == mapped_id,
                PublicPromptPack.enabled == True,
            ).first()
        else:
            pack = db.query(PublicPromptPack).filter(
                PublicPromptPack.scope == scope,
                PublicPromptPack.enabled == True,
            ).first()

    if not pack:
        return {
            "tool": "get_prompt_pack",
            "status": "skipped",
            "detail": f"Prompt pack not found: scope={scope} mode={mode} pack_id={pack_id}",
            "data": None,
        }

    # For chapter_writing packs, build system_prompt from shared source
    # (same modules as internal packs — edit once, both benefit)
    system_prompt = pack.system_prompt
    if pack.pack_id in ("chapter_writing_quality", "chapter_writing_fast"):
        from app.prompts.prompt_source import (
            get_public_chapter_quality_system_prompt,
            get_public_chapter_fast_system_prompt,
        )
        from app.prompts.style_prompts import build_style_context
        from app.database.models import Project
        project = db.query(Project).filter(Project.id == project_id).first() if project_id else None
        if project:
            style_ctx = build_style_context(project, include_anti_ai=True)
            if pack.pack_id == "chapter_writing_quality":
                system_prompt = get_public_chapter_quality_system_prompt()
            else:
                system_prompt = get_public_chapter_fast_system_prompt()
            system_prompt = system_prompt.replace("{style_context}", style_ctx)

    return {
        "tool": "get_prompt_pack",
        "status": "ok",
        "detail": f"Prompt pack: {pack.title} (v{pack.version})",
        "data": {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "scope": pack.scope,
            "title": pack.title,
            "summary": pack.summary,
            "system_prompt": system_prompt,
            "workflow": pack.workflow_json,
            "quality_rubric": pack.quality_rubric_json,
            "tool_playbook": pack.tool_playbook_json,
            "forbidden_patterns": pack.forbidden_patterns_json,
            "context_policy": pack.context_policy_json,
            "output_contract": pack.output_contract_json,
        },
    }


async def get_tool_playbook(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Get a tool usage playbook for a specific scenario."""
    from app.database.models import PublicPromptPack
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    tool_name = str(args.get("tool_name") or "").strip()
    scenario = str(args.get("scenario") or "external_writing").strip()

    if not tool_name:
        return {
            "tool": "get_tool_playbook",
            "status": "skipped",
            "detail": "tool_name is required",
            "data": None,
        }

    # Search all packs for the tool playbook
    packs = db.query(PublicPromptPack).filter(
        PublicPromptPack.enabled == True,
        PublicPromptPack.tool_playbook_json != None,
    ).all()

    for pack in packs:
        playbook = pack.tool_playbook_json or {}
        if tool_name in playbook:
            entry = playbook[tool_name]
            return {
                "tool": "get_tool_playbook",
                "status": "ok",
                "detail": f"Playbook for {tool_name} from {pack.pack_id}",
                "data": {
                    "tool_name": tool_name,
                    "scenario": scenario,
                    "pack_id": pack.pack_id,
                    "playbook": entry,
                },
            }

    return {
        "tool": "get_tool_playbook",
        "status": "skipped",
        "detail": f"No playbook found for tool: {tool_name}",
        "data": None,
    }


async def get_quality_rubric(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Get quality rubric for a specific scope."""
    from app.database.models import PublicPromptPack
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scope = str(args.get("scope") or "chapter_writing").strip()
    pack_id = str(args.get("pack_id") or "").strip()

    if pack_id:
        pack = db.query(PublicPromptPack).filter(
            PublicPromptPack.pack_id == pack_id,
            PublicPromptPack.enabled == True,
        ).first()
    else:
        # Find the quality pack for this scope
        scope_pack_map = {
            "chapter_writing": "chapter_writing_quality",
            "chapter_review": "chapter_review_quality",
        }
        mapped_id = scope_pack_map.get(scope, "")
        if mapped_id:
            pack = db.query(PublicPromptPack).filter(
                PublicPromptPack.pack_id == mapped_id,
                PublicPromptPack.enabled == True,
            ).first()
        else:
            pack = db.query(PublicPromptPack).filter(
                PublicPromptPack.scope == scope,
                PublicPromptPack.enabled == True,
                PublicPromptPack.quality_rubric_json != None,
            ).first()

    if not pack or not pack.quality_rubric_json:
        return {
            "tool": "get_quality_rubric",
            "status": "skipped",
            "detail": f"No quality rubric found for scope: {scope}",
            "data": None,
        }

    return {
        "tool": "get_quality_rubric",
        "status": "ok",
        "detail": f"Quality rubric from {pack.pack_id}",
        "data": {
            "pack_id": pack.pack_id,
            "scope": pack.scope,
            "rubric": pack.quality_rubric_json,
        },
    }
