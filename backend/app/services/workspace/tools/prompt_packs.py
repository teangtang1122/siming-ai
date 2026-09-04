"""Prompt pack tools — read public prompt packs and method cards.

These tools are API-free and exposed to internal assistant, scheduler,
and MCP readonly collaboration pack.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


INTERNAL_LLM_TOOLS = [
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


async def get_moshu_usage_guide(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Return the external-agent quickstart and scenario workflows.

    This tool is intentionally API-free. It gives Claude Code, Codex, and the
    in-app assistant a deterministic first stop when they do not know which
    Siming workflow to use.
    """
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    scenario = str(args.get("scenario") or "quickstart").strip() or "quickstart"
    no_api = bool(
        args.get("no_api")
        if "no_api" in args
        else scenario in {"cataloging_no_api", "writing_no_api", "outline_no_api"}
    )

    workflows = {
        "quickstart": {
            "title": "司命外部 Agent 快速入口",
            "rules": [
                "Siming 2.1 uses the database as the authoritative source. The project folder is a read-only mirror for context: chapters/*.md, characters/*.json, worldbuilding/**/*.json, outline/outline.json, relationships/relationships.json.",
                "When you need long text or exact source material, use get_project_files_info -> list_project_files/read_project_file/search_project_files, or read the mirrored files directly if you are a local CLI agent.",
                "Trusted local mode is the default. Do not ask the user to approve tool calls in the Siming web UI; there is no per-call approval UI. Use the available file reads and Siming MCP tools directly, and report any real permission error as a configuration problem.",
                "If the task is only information lookup and you are a local CLI agent, prefer reading the mirrored project files first. Use Siming MCP tools for writes, deletes, verification, progress reporting, and when the file mirror is missing or stale.",
                "Do not edit project files directly. All writes/deletes/updates must use Siming MCP/API tools; Siming will refresh the file mirror after database writes.",
                "默认使用 API-free 外部流程：除非用户明确说“使用司命内部 API/内部模型/系统模型额度”，不要调用内部模型工具。",
                "如果用户希望 Siming 代为启动本机 Claude/Codex/opencode，而不是手动在外部 Agent 中操作，调用 start_local_cli_agent_run。",
                "内部模型工具现在只通过 MCP permission pack: internal_llm 暴露；project_management 只用于 API-free 的项目创建、导入、写入、导出、技能和自动任务管理。",
                "中文小说必须用中文保存角色名、别名、章节标题、摘要、大纲、事实和世界观；不要因为工具报错就改成英文或拼音。",
                "先调用 list_projects 或 get_project_info 确认作品；所有项目写入工具都必须传入正确 project_id。",
                "创建、导入、建档、写作后必须调用 get_project_archive_status 或对应 search/list 工具验证数据真的写入到了目标作品。",
                "长正文、完整章节、完整档案和大量候选 JSON 不要完整输出到聊天里；应写入 save_external_chapter_draft、save_external_cataloging_candidates 或对应写入工具，聊天只返回摘要、ID、字数、数量和验证结果。",
            ],
            "first_tools": [
                "get_mcp_permission_status",
                "list_projects",
                "get_project_files_info",
                "list_project_files",
                "read_project_file",
                "search_project_files",
                "get_project_archive_status",
                "list_prompt_packs",
                "get_prompt_pack",
            ],
            "internal_llm_tools_forbidden_by_default": INTERNAL_LLM_TOOLS,
        },
        "import_file": {
            "title": "导入本地小说为新作品",
            "steps": [
                "调用 import_file_as_project(file_path, title)。",
                "读取返回的 project.id；之后所有写入都使用这个 project_id。",
                "调用 get_project_archive_status 验证 chapters_count 是否正确。",
                "如果用户还要建档，默认继续 cataloging_no_api；只有用户明确授权内部 API 时才走 cataloging_internal。",
                "如果用户希望 Siming 启动本机 CLI 完成后续建档，调用 start_local_cli_agent_run(task_type='cataloging')。",
            ],
        },
        "cataloging_no_api": {
            "title": "API-free 建档，由外部 Agent 自己读章节并写入",
            "steps": [
                "如果需要 Siming 代为启动本机 CLI，调用 start_local_cli_agent_run(task_type='cataloging')，再通过 AgentRun 事件查看进度。",
                "调用 get_prompt_pack(pack_id='cataloging_external_no_api') 读取建档提示词和输出契约。",
                "调用 start_external_cataloging_job 创建外部建档任务。",
                "逐章调用 get_next_external_cataloging_chapter(phase='facts')，只读当前章并调用 save_external_cataloging_facts。",
                "再领取同一章 phase='candidates'，调用 list_cataloging_facts，读取当前档案镜像并调用 save_external_cataloging_candidates -> apply_pending_cataloging。",
                "每章 apply 后调用 verify_external_cataloging_progress；前一章完成前不得处理下一章。发现缺项时只补工具明确列出的缺项。",
                "最终调用 get_project_archive_status，确认角色、大纲、世界观、章节摘要数量符合预期后再报告完成。",
            ],
            "forbidden_tools": INTERNAL_LLM_TOOLS,
        },
        "cataloging_internal": {
            "title": "使用司命内部 API 建档",
            "steps": [
                "只有用户明确授权使用司命内部 API/内部模型时才能进入此流程。",
                "确认 MCP 权限包为 internal_llm，且系统设置里的模型 API 可用。",
                "调用 start_cataloging_job；前端会显示实时进度。",
                "失败时使用 retry_current_cataloging_chapter 或 rerun_cataloging_resolution_current。",
                "完成后调用 get_project_archive_status 验证数据。",
            ],
        },
        "writing_no_api": {
            "title": "API-free 写作，由外部 Agent 生成正文",
            "steps": [
                "查询真实章级大纲 ID；界面选中项和历史消息不能替代最新用户目标。",
                "调用 prepare_external_writing_context 建立只含目标、文风、作者要求和显式固定项的精简基线。",
                "模型按需调用 search_task_context，复核候选后调用 submit_context_evidence；确实不需要额外资料时提交空数组。",
                "submit_context_evidence 返回首个 context_page；若 has_more=true，逐次原样复制 next_arguments 调用 prepare_task_context，末页才返回 context_selection_token。",
                "读完全部精确上下文后，在下一模型步骤一次生成未入库草稿。",
                "携带同一 manifest 与 context_selection_token 调用 save_external_chapter_draft，然后立即结束本轮。",
                "不得继续写入正式章节、角色或世界观，也不得启动或查询建档。",
                "正式保存与启动建档由作者在界面选择；去除 AI 味和质量评分直接读取编辑器当前草稿。",
            ],
            "forbidden_tools": INTERNAL_LLM_TOOLS,
        },
        "outline_no_api": {
            "title": "API-free 大纲提案，由外部 Agent 生成并交作者确认",
            "steps": [
                "查询真实父节点与插入位置；应用层不根据关键词或界面状态猜目标。",
                "调用 prepare_task_context(task_type='outline_planning', parent_id=..., insert_after_id=..., batch_count=..., requirements=...) 建立精简基线。",
                "模型按需调用 search_task_context，复核候选后调用 submit_context_evidence。",
                "按 next_arguments 逐页读完 context_page；末页取得选择令牌后，才在下一模型步骤生成章级节点及其 section。",
                "携带同一 manifest 与 context_selection_token 调用 save_external_outline_draft，然后立即结束本轮。",
                "不得调用 create_outline_nodes 或继续写正文；作者可编辑、确认、重新生成或放弃提案。",
            ],
            "forbidden_tools": INTERNAL_LLM_TOOLS,
        },
        "writing_internal": {
            "title": "使用司命内部 API 写作",
            "steps": [
                "只有用户明确授权使用司命内部 API/内部模型时才能进入此流程。",
                "确认 MCP 权限包为 internal_llm。",
                "内部写作只执行检索上下文和一次生成正文，并将结果作为未入库草稿载入编辑器后立即结束。",
                "章节正文只使用质量提示词。正式保存和建档由作者在界面决定。",
                "去除 AI 味和质量评审是独立操作，只在用户另行发起时执行。",
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

def _recommended_next(scenario: str, no_api: bool) -> list[dict[str, Any]]:
    if scenario == "cataloging_no_api":
        return [
            {"tool": "get_prompt_pack", "arguments": {"pack_id": "cataloging_external_no_api"}},
            {"tool": "start_external_cataloging_job", "arguments": {}},
        ]
    if scenario == "writing_no_api":
        return [
            {"tool": "get_prompt_pack", "arguments": {"pack_id": "chapter_writing_quality"}},
            {"tool": "prepare_external_writing_context", "arguments": {}},
        ]
    if scenario == "outline_no_api":
        return [
            {"tool": "get_prompt_pack", "arguments": {"pack_id": "outline_planning"}},
            {"tool": "prepare_task_context", "arguments": {"task_type": "outline_planning"}},
        ]
    if no_api:
        return [{"tool": "get_mcp_permission_status", "arguments": {}}]
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

    requested_pack_id = pack_id
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
    effective_pack_id = pack.pack_id
    requested_pack_id = requested_pack_id or pack.pack_id
    if pack.pack_id == "chapter_writing_quality":
        from app.prompts.prompt_source import get_public_chapter_quality_system_prompt
        from app.prompts.style_prompts import build_style_context
        from app.database.models import Project

        system_prompt = get_public_chapter_quality_system_prompt()
        project = db.query(Project).filter(Project.id == project_id).first() if project_id else None
        if project:
            style_ctx = build_style_context(project, include_anti_ai=False)
            system_prompt = system_prompt.replace("{style_context}", style_ctx)

    quality_rubric = pack.quality_rubric_json
    forbidden_patterns = pack.forbidden_patterns_json
    if pack.pack_id == "chapter_writing_quality":
        quality_rubric = None
        forbidden_patterns = []

    return {
        "tool": "get_prompt_pack",
        "status": "ok",
        "detail": f"Prompt pack: {pack.title} (v{pack.version})",
        "data": {
            "pack_id": pack.pack_id,
            "requested_pack_id": requested_pack_id,
            "effective_pack_id": effective_pack_id,
            "version": pack.version,
            "scope": pack.scope,
            "title": pack.title,
            "summary": pack.summary,
            "system_prompt": system_prompt,
            "workflow": pack.workflow_json,
            "quality_rubric": quality_rubric,
            "tool_playbook": pack.tool_playbook_json,
            "forbidden_patterns": forbidden_patterns,
            "context_policy": pack.context_policy_json,
            "output_contract": pack.output_contract_json,
            "prompt_spec": pack.tags_json if isinstance(pack.tags_json, dict) else None,
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
