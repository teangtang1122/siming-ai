# ruff: noqa: E501
"""Continuity workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef
from app.modules.continuity.domain.cataloging_contract import CATALOGING_FACT_TYPES
from app.modules.story.interfaces.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from app.services.task_context_delivery import CONTEXT_PAGE_INPUTS

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="start_cataloging_job",
        description="Start a project cataloging job that initializes or updates chapter summaries, characters, outline, worldbuilding, and links from existing chapters.",
        input_schema={
            "execution_mode": {
                "type": "string",
                "description": "auto or manual; manual waits for user confirmation after each chapter",
            },
            "model": {"type": "string", "description": "Optional model override"},
            "chapter_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional ordered chapter IDs; omit for all chapters",
            },
            "run_now": {
                "type": "boolean",
                "description": "Start processing immediately; default true",
            },
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="high",
        handler_name="start_cataloging_job",
    ),
    ToolDef(
        name="list_cataloging_jobs",
        description="List recent project cataloging jobs and their progress.",
        input_schema={
            "limit": {"type": "integer", "description": "Maximum jobs to return; default 20"}
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="list_cataloging_jobs",
    ),
    ToolDef(
        name="get_cataloging_job",
        description="Read a cataloging job with its chapter runs.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_cataloging_job",
    ),
    ToolDef(
        name="get_cataloging_control_state",
        description="Read the compact live control state for a cataloging job, including auto/manual mode.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_cataloging_control_state",
    ),
    ToolDef(
        name="set_cataloging_mode",
        description="Switch a cataloging job between auto and manual confirmation mode.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "execution_mode": {"type": "string", "description": "auto or manual"},
        },
        required=["job_id", "execution_mode"],
        tool_type="write",
        estimated_cost="free",
        handler_name="set_cataloging_mode",
    ),
    ToolDef(
        name="list_cataloging_candidates",
        description="Read a complete page of cataloging candidates. Follow next_arguments while has_more is true; reduce limit if a page exceeds capacity.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_run_id": {"type": "string", "description": "Optional chapter run ID"},
            "status": {"type": "string", "description": "Optional candidate status filter"},
            "item_type": {"type": "string", "description": "Optional candidate type filter"},
            "offset": {"type": "integer", "minimum": 0, "description": "Zero-based item offset; default 0"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Complete items per page; default 2"},
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="list_cataloging_candidates",
    ),
    ToolDef(
        name="list_cataloging_facts",
        description="Read a complete page of saved first-stage facts. Follow next_arguments while has_more is true before resolving candidates; reduce limit if a page exceeds capacity.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_run_id": {"type": "string", "description": "Optional chapter run ID"},
            "fact_type": {"type": "string", "description": "Optional fact type filter"},
            "offset": {"type": "integer", "minimum": 0, "description": "Zero-based item offset; default 0"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Complete items per page; default 2"},
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="list_cataloging_facts",
    ),
    ToolDef(
        name="update_cataloging_candidate",
        description="Edit or approve/reject a cataloging candidate before it is applied.",
        input_schema={
            "candidate_id": {"type": "string", "description": "Candidate ID"},
            "payload": {"type": "object", "description": "Edited candidate payload"},
            "status": {
                "type": "string",
                "description": "pending|edited|approved|rejected|applying|applied|apply_failed",
            },
        },
        required=["candidate_id"],
        tool_type="write",
        estimated_cost="free",
        handler_name="update_cataloging_candidate",
    ),
    ToolDef(
        name="apply_pending_cataloging",
        description="Apply the current waiting-confirmation cataloging chapter candidates and continue the job when possible.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        estimated_cost="free",
        handler_name="apply_pending_cataloging",
    ),
    ToolDef(
        name="retry_current_cataloging_chapter",
        description="Retry the failed or waiting current cataloging chapter from stage one.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {
                "type": "boolean",
                "description": "Resume processing immediately; default true",
            },
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="high",
        handler_name="retry_current_cataloging_chapter",
    ),
    ToolDef(
        name="rerun_cataloging_resolution_current",
        description="Retry only the second cataloging stage for the current chapter, reusing saved facts.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {
                "type": "boolean",
                "description": "Resume processing immediately; default true",
            },
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="medium",
        handler_name="rerun_cataloging_resolution_current",
    ),
    ToolDef(
        name="pause_cataloging_job",
        description="Pause a running cataloging job.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        estimated_cost="free",
        handler_name="pause_cataloging_job",
    ),
    ToolDef(
        name="resume_cataloging_job",
        description="Resume a paused cataloging job and continue processing.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "run_now": {
                "type": "boolean",
                "description": "Resume processing immediately; default true",
            },
        },
        required=["job_id"],
        tool_type="write",
        estimated_cost="high",
        handler_name="resume_cataloging_job",
    ),
    ToolDef(
        name="cancel_cataloging_job",
        description="Cancel a cataloging job. Requires clear user confirmation.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler_name="cancel_cataloging_job",
    ),
    ToolDef(
        name="preview_deconstruct_source",
        description="Preview available chapters and word counts before starting legacy deconstruct analysis.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="preview_deconstruct_source",
    ),
    ToolDef(
        name="list_deconstruct_reports",
        description="List persisted legacy deconstruct reports.",
        input_schema={
            "limit": {"type": "integer", "description": "Maximum reports to return; default 20"}
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="list_deconstruct_reports",
    ),
    ToolDef(
        name="get_deconstruct_report",
        description="Read a persisted legacy deconstruct report.",
        input_schema={"report_id": {"type": "string", "description": "Deconstruct report ID"}},
        required=["report_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_deconstruct_report",
    ),
    ToolDef(
        name="start_deconstruct_job",
        description="Start legacy deconstruct analysis for selected chapters or pasted text. Prefer cataloging for project initialization.",
        input_schema={
            "text": {"type": "string", "description": "Optional text to analyze"},
            "chapter_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Existing chapters to analyze",
            },
            "title": {"type": "string", "description": "Report title"},
            "model": {"type": "string", "description": "Optional model override"},
            "map_model": {"type": "string", "description": "Optional map model"},
            "reduce_model": {"type": "string", "description": "Optional reduce model"},
            "analysis_mode": {"type": "string", "description": "fast or detailed; default fast"},
            "include_golden_three": {
                "type": "boolean",
                "description": "Whether to analyze first three chapters",
            },
            "include_rhythm": {
                "type": "boolean",
                "description": "Whether to include rhythm analysis",
            },
            "include_patterns": {
                "type": "boolean",
                "description": "Whether to include writing-pattern analysis",
            },
            "map_concurrency": {"type": "integer", "description": "Map concurrency 1-12"},
            "run_now": {
                "type": "boolean",
                "description": "Start processing immediately; default true",
            },
        },
        tool_type="write",
        idempotent=True,
        estimated_cost="high",
        handler_name="start_deconstruct_job",
    ),
    ToolDef(
        name="rerun_failed_deconstruct_chunks",
        description="Rerun only failed chunks for an existing legacy deconstruct report.",
        input_schema={
            "report_id": {"type": "string", "description": "Deconstruct report ID"},
            "model": {"type": "string", "description": "Optional model override"},
            "map_model": {"type": "string", "description": "Optional map model"},
            "reduce_model": {"type": "string", "description": "Optional reduce model"},
        },
        required=["report_id"],
        tool_type="write",
        estimated_cost="high",
        handler_name="rerun_failed_deconstruct_chunks",
    ),
    ToolDef(
        name="import_deconstruct_report",
        description="Import selected sections from a deconstruct report into outline, characters, and/or worldbuilding.",
        input_schema={
            "report_id": {"type": "string", "description": "Deconstruct report ID"},
            "import_outline": {"type": "boolean", "description": "Import outline nodes"},
            "import_characters": {"type": "boolean", "description": "Import characters"},
            "import_worldbuilding": {
                "type": "boolean",
                "description": "Import worldbuilding entries",
            },
        },
        required=["report_id"],
        tool_type="write",
        estimated_cost="low",
        handler_name="import_deconstruct_report_tool",
    ),
    ToolDef(
        name="suggest_conflicts",
        description="基于当前剧情状态生成3种情节冲突建议（人物冲突/势力冲突/内心冲突）。用户说'设计冲突''加点矛盾'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "prompt": {"type": "string", "description": "用户倾向或额外上下文，可选"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="suggest_conflicts",
    ),
    ToolDef(
        name="detect_character_changes",
        description="只读检测章节中追踪角色的变化（技能/经历/关系/性格）。可传draft_id/content_ref、content+title或chapter_id；不会写入角色、变化日志、时间线或章节关联，正式正文的衍生数据统一由作者启动的建档任务处理。",
        input_schema={
            "content": {
                "type": "string",
                "description": "章节正文（检测未保存的正文时使用，与title配合）",
            },
            "draft_id": {
                "type": "string",
                "description": "chapter_writer返回的草稿ID，可替代content",
            },
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {
                "type": "string",
                "description": "已保存的章节ID（只读取正文进行分析，不写入任何数据）",
            },
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="detect_character_changes",
    ),
    ToolDef(
        name="detect_new_worldbuilding",
        description="检测章节正文中引入的新世界观设定——对照已有设定条目，找出正文中出现但尚未录入数据库的地点、规则、势力、种族、文化习俗等。只读不写，返回建议条目列表和原文参考。可传draft_id/content_ref或chapter_id，避免复制长正文。",
        input_schema={
            "content": {
                "type": "string",
                "description": "章节正文（可选；优先用draft_id或chapter_id）",
            },
            "draft_id": {
                "type": "string",
                "description": "chapter_writer返回的草稿ID，可替代content",
            },
            "content_ref": {"type": "string", "description": "同draft_id"},
            "chapter_id": {"type": "string", "description": "已保存章节ID，可替代content"},
            "title": {"type": "string", "description": "章节标题（可选）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="detect_new_worldbuilding",
    ),
    ToolDef(
        name="detect_worldbuilding_conflicts",
        description="检测全部世界观条目之间的逻辑矛盾、规则冲突、时间线不一致。",
        input_schema={},
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="detect_worldbuilding_conflicts",
    ),
    ToolDef(
        name="detect_forbidden_patterns",
        description="检测文本中的禁用句式（如'仿佛''不由得''很愤怒'等70+种AI高频套话）。纯规则匹配，不调LLM。",
        input_schema={"text": {"type": "string", "description": "要检测的文本"}},
        required=["text"],
        tool_type="analysis",
        estimated_cost="free",
        handler_name="detect_forbidden_patterns",
    ),
    ToolDef(
        name="evaluate_chapter",
        description="对章节正文进行8维度80分评估（开头吸引力/情节推进/角色塑造/对话质量/悬念设置/节奏控制/展示性描写/语言质量）。传入draft_id/content_ref或content+title评估未保存正文，或传入chapter_id评估已保存章节。",
        input_schema={
            "content": {
                "type": "string",
                "description": "章节正文（评估未保存的正文时使用，与chapter_id二选一）",
            },
            "draft_id": {
                "type": "string",
                "description": "chapter_writer返回的草稿ID，可替代content",
            },
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {
                "type": "string",
                "description": "已保存的章节ID（评估已保存的章节时使用）",
            },
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="evaluate_chapter",
    ),
    ToolDef(
        name="prepare_external_writing_context",
        description="Prepare governed writing context for a model-selected chapter or the current pending draft. To revise that draft, pass its real source_draft_id; its full current text becomes a required, hash-checked context anchor. Writing instructions and source text are returned only in context_page.text; follow the next_tool_suggestions arguments until has_more=false. Then search and submit exact evidence, read all selected-context pages, and use the selection token. API-free and never overwrites formal prose.",
        input_schema={
            "outline_node_id": {"type": "string", "description": "Target outline node ID"},
            "target_chapter_id": {
                "type": "string",
                "description": "Existing chapter ID when preparing a reviewable revision",
            },
            "source_draft_id": {
                "type": "string",
                "description": "Current pending draft ID when revising the same unsaved draft",
            },
            "include_prompt_pack": {
                "type": "boolean",
                "description": "Include public prompt pack (default true)",
            },
            "requirements": {"type": "string", "description": "Additional writing requirements"},
            "minimum_han_characters": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100000,
                "description": "Model-structured hard minimum when the author explicitly requires a Chinese-body length; enforced before any draft is stored and never inferred from requirements text",
            },
            "context_manifest_id": {
                "type": "string",
                "description": "Prepared governed baseline manifest ID; reuse after model-driven retrieval",
            },
            "model": {
                "type": "string",
                "description": "Model identity for unbound external agents; managed CLI runs use their pinned executing model for the context budget",
            },
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}},
            "pinned_source_ids": {"type": "array", "items": {"type": "string"}},
            **CONTEXT_PAGE_INPUTS,
        },
        required=["outline_node_id"],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="prepare_external_writing_context",
    ),
    ToolDef(
        name="save_external_chapter_draft",
        description="Save one not-yet-official new-chapter draft, reviewable existing-chapter revision, or replace the exact current pending draft and end the model turn. Pending-draft revision requires the same source_draft_id used for context and is rejected if the author changed it meanwhile. This tool never overwrites saved prose.",
        input_schema={
            "content": {"type": "string", "description": "Chapter content to save"},
            "outline_node_id": {"type": "string", "description": "Linked outline node ID"},
            "target_chapter_id": {
                "type": "string",
                "description": "Existing chapter ID for a revision candidate",
            },
            "base_chapter_version": {
                "type": "integer",
                "description": "Version returned by prepare_external_writing_context for conflict protection",
            },
            "source_draft_id": {
                "type": "string",
                "description": "Current pending draft ID to replace after exact-context revision",
            },
            "context_manifest_id": {
                "type": "string",
                "description": "Prepared governed baseline manifest ID",
            },
            "context_selection_token": {
                "type": "string",
                "description": "Token returned by submit_context_evidence after exact source selection",
            },
            "source_agent": {
                "type": "string",
                "description": "Source agent name (e.g. claude-code)",
            },
        },
        required=["content", "outline_node_id", "context_manifest_id", "context_selection_token"],
        tool_type="write",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        ends_agent_turn=True,
        handler_name="save_external_chapter_draft",
    ),
    ToolDef(
        name="get_external_chapter_draft",
        description="Get a chapter draft by ID, or omit the ID to discover the current pending draft. API-free.",
        input_schema={
            "draft_id": {"type": "string", "description": "Draft ID to retrieve"},
            "content_ref": {"type": "string", "description": "Alias for draft_id"},
        },
        required=[],
        tool_type="read",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        handler_name="get_external_chapter_draft",
    ),
    ToolDef(
        name="save_external_outline_draft",
        description="Save one author-visible, not-yet-formal outline proposal from exact model-selected context and end the turn. The author must confirm it before formal outline nodes exist.",
        input_schema={
            "nodes": {
                "type": "array",
                "items": {"type": "object"},
                "minItems": 1,
                "maxItems": OUTLINE_PROPOSAL_MAX_NODES,
                "description": f"One to {OUTLINE_PROPOSAL_MAX_NODES} proposed outline nodes",
            },
            "design_notes": {"type": "string"},
            "parent_id": {"type": "string"},
            "insert_after_id": {"type": "string"},
            "context_manifest_id": {"type": "string"},
            "context_selection_token": {"type": "string"},
        },
        required=["nodes", "context_manifest_id", "context_selection_token"],
        tool_type="write",
        direct_mcp_project_scoped=True,
        direct_mcp_transactional=True,
        estimated_cost="free",
        ends_agent_turn=True,
        handler_name="save_external_outline_draft",
    ),
    ToolDef(
        name="record_external_quality_review",
        description="Record a quality review from an external agent. API-free. Stores review scores, issues, and suggestions.",
        input_schema={
            "draft_id": {"type": "string", "description": "Draft ID to review"},
            "content_ref": {"type": "string", "description": "Alias for draft_id"},
            "chapter_id": {"type": "string", "description": "Chapter ID to review"},
            "scores": {"type": "object", "description": "Score dict: {dimension: score}"},
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of issues found",
            },
            "revision_suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Suggested revisions",
            },
            "pass": {"type": "boolean", "description": "Whether the review passes"},
            "reviewer_model": {"type": "string", "description": "Model that did the review"},
            "prompt_pack_version": {"type": "string", "description": "Prompt pack version used"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="record_external_quality_review",
    ),
    ToolDef(
        name="update_narrative_ledger_entry",
        description="Manually revise or invalidate one narrative ledger entry while preserving its prior fact version.",
        input_schema={
            "entry_id": {"type": "string", "description": "Narrative ledger entry ID"},
            "title": {"type": "string", "description": "Optional corrected title"},
            "status": {
                "type": "string",
                "description": "Optional lifecycle status, such as active, open, fulfilled, invalidated",
            },
            "storyline": {"type": "string", "description": "Optional corrected storyline"},
            "note": {
                "type": "string",
                "description": "Reason or evidence for this manual revision",
            },
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler_name="update_narrative_ledger_entry",
    ),
    ToolDef(
        name="get_narrative_ledger",
        description="Read active completed beats, revealed clues, narrative promises, and storyline states.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID"},
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "completed_beat|revealed_clue|narrative_promise|storyline_state",
            },
            "statuses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional lifecycle statuses",
            },
            "storyline": {"type": "string", "description": "Optional storyline filter"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="get_narrative_ledger",
    ),
    ToolDef(
        name="get_narrative_governance",
        description="Read structured foreshadowings, causal edges, narrative debts, character dynamic state, quality metrics, and narrative checkpoints.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional current chapter ID"},
            "view": {
                "type": "string",
                "enum": ["all", "chapter", "due", "risk"],
                "description": "Dashboard filter",
            },
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="get_narrative_governance",
    ),
    ToolDef(
        name="apply_narrative_governance_candidates",
        description="Preview or apply structured narrative governance candidates after cataloging or chapter writing.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Source chapter ID"},
            "mode": {
                "type": "string",
                "enum": ["preview", "apply"],
                "description": "Preview is non-mutating; apply writes project state",
            },
            "candidates": {
                "type": "array",
                "items": {"type": "object"},
                "description": "foreshadowing, causal_edge, narrative_debt, character_state, or quality_metric candidates",
            },
        },
        required=["candidates"],
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler_name="apply_narrative_governance_candidates",
    ),
    ToolDef(
        name="list_narrative_checkpoints",
        description="List linear project-level narrative checkpoints.",
        input_schema={"limit": {"type": "integer"}},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_narrative_checkpoints",
    ),
    ToolDef(
        name="diff_narrative_checkpoint",
        description="Compare current structured narrative state with a saved checkpoint.",
        input_schema={"checkpoint_id": {"type": "string"}},
        required=["checkpoint_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="diff_narrative_checkpoint",
    ),
    ToolDef(
        name="restore_narrative_governance_checkpoint",
        description="Restore structured narrative state from a project checkpoint. Requires write confirmation under the active MCP permission policy.",
        input_schema={"checkpoint_id": {"type": "string"}},
        required=["checkpoint_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="high",
        requires_confirmation=True,
        estimated_cost="free",
        handler_name="restore_narrative_governance_checkpoint",
    ),
    ToolDef(
        name="inspect_story_granularity",
        description="Audit project or chapter story granularity: summaries, chapter outline, section events, narrative facts, character states, and links.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID to audit"},
            "level": {"type": "string", "description": "basic|narrative. Default narrative."},
            "limit": {"type": "integer", "description": "Maximum chapters to audit, default 200"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="inspect_story_granularity",
    ),
    ToolDef(
        name="repair_story_granularity",
        description="Create post-write archive runs to repair missing story granularity. Defaults to manual candidate review.",
        input_schema={
            "chapter_id": {"type": "string", "description": "Optional chapter ID to repair"},
            "limit": {
                "type": "integer",
                "description": "Maximum chapters to inspect/repair, default 20",
            },
            "mode": {"type": "string", "description": "manual|auto. Manual is default."},
            "repair_level": {
                "type": "string",
                "description": "basic|narrative. Basic is default; narrative only when explicitly requested.",
            },
            "force": {
                "type": "boolean",
                "description": "Repair even chapters that currently pass the audit",
            },
            "model": {
                "type": "string",
                "description": "Optional model for repair candidate generation",
            },
        },
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="model_or_free",
        handler_name="repair_story_granularity",
    ),
    ToolDef(
        name="start_external_cataloging_job",
        description="Create a cataloging job for external agent mode. API-free. Creates one chapter run per chapter.",
        input_schema={
            "chapter_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional chapter IDs to catalog. Omit for all chapters.",
            }
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="start_external_cataloging_job",
    ),
    ToolDef(
        name="get_next_external_cataloging_chapter",
        description="Get the next pending chapter for external cataloging. Returns chapter text, character/wb indexes, and prompt pack. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "phase": {
                "type": "string",
                "enum": ["facts", "candidates"],
                "description": "facts extracts the current chapter; candidates resolves its saved facts against the current archive.",
            },
            "include_content": {
                "type": "boolean",
                "description": "Whether to return chapter text in the tool result. Set false when the Agent can read content_file_path directly.",
            },
            "include_prompt_pack": {
                "type": "boolean",
                "description": "Whether to include the full prompt pack in the tool result. Set false when the task file already contains the shared prompt.",
            },
            "include_context_indexes": {
                "type": "boolean",
                "description": "Whether to return character/worldbuilding/outline indexes. Set false when the Agent can search the project mirror directly.",
            },
        },
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_next_external_cataloging_chapter",
    ),
    ToolDef(
        name="save_external_cataloging_facts",
        description="Save facts extracted by the external model. API-free.",
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_id": {"type": "string", "description": "Chapter ID"},
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_type": {"type": "string", "enum": list(CATALOGING_FACT_TYPES)},
                        "payload": {"type": "object"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["fact_type", "payload"],
                },
                "description": "Extracted records with fact_type and object payload. Include exactly one chapter_overview whose four required scope arrays exactly match the stable/non-archival character and worldbuilding facts. Relationship endpoints must be stable characters in cataloging_characters. Do not send flat untyped facts or duplicate identities.",
            },
        },
        required=["job_id", "chapter_id", "facts"],
        tool_type="write",
        writes_project_data=True,
        risk_level="low",
        estimated_cost="free",
        handler_name="save_external_cataloging_facts",
    ),
    ToolDef(
        name="save_external_cataloging_candidates",
        description=(
            "Save one incremental batch toward a chapter's complete candidate set. "
            "For Siming-managed jobs each call accepts at most 3 records; the first call "
            "must contain exactly chapter_summary and the chapter-level outline. Continue "
            "only with returned missing items. A later single chapter_summary may set "
            "coverage_manifest_mode='replace' with all five manifest fields to remove an "
            "overdeclared alias without changing scene_count. A later single chapter_link may set "
            "chapter_link_mode='replace' with all five aggregate arrays to remove a wrong alias or "
            "endpoint from the retained link. When a source fact label resolves to a differently "
            "titled active world card, put that exact label in the card's source_fact_titles. "
            "A state update that changes an existing "
            "appearance or age must include the exact current *_before value and a verbatim chapter "
            "*_evidence excerpt. A state update that changes an existing "
            "non-empty items_or_assets must copy it exactly to items_or_assets_before and retain it in "
            "the new full value. Empty entity lists are valid. API-free."
        ),
        input_schema={
            "job_id": {"type": "string", "description": "Cataloging job ID"},
            "chapter_id": {"type": "string", "description": "Chapter ID"},
            "candidates": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Native candidate objects for the next incremental batch. Siming-managed "
                    "calls allow at most 3; the first call is exactly chapter_summary plus the "
                    "chapter-level outline. A manifest replacement must be the only record in "
                    "its call and include scene_count, characters, worldbuilding, relationships, "
                    "and character_profiles. A chapter-link replacement must be the only record "
                    "in its call and include characters, worldbuilding_titles, locations, items, "
                    "and events."
                ),
            },
        },
        required=["job_id", "chapter_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="low",
        estimated_cost="free",
        handler_name="save_external_cataloging_candidates",
    ),
    ToolDef(
        name="verify_external_cataloging_progress",
        description="Verify cataloging progress with counts and samples. API-free.",
        input_schema={"job_id": {"type": "string", "description": "Cataloging job ID"}},
        required=["job_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="verify_external_cataloging_progress",
    ),
    ToolDef(
        name="get_project_archive_status",
        description="Get project archive status: chapter/character/outline/worldbuilding counts, last cataloging job, warnings, and recommended next steps. Use to verify project data exists before reporting completion.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="get_project_archive_status",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
