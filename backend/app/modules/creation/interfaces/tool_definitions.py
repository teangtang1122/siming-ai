# ruff: noqa: E501
"""Creation workspace tool declarations."""

from __future__ import annotations

from app.architecture.tool_definition import ToolDef

TOOL_DEFINITIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name="design_plot",
        description="设计完整章节剧情——含场景拆解、角色行为、冲突张力、情绪曲线、一致性检查等7个维度。用户说'设计剧情''这章怎么写'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "involved_characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "出场的角色名或ID列表，可选",
            },
            "requirements": {"type": "string", "description": "用户的额外要求，可选"},
            "feedback": {"type": "string", "description": "对上一轮设计的反馈（迭代时使用），可选"},
            "previous_plot": {
                "type": "string",
                "description": "上一轮设计的剧情（迭代时使用），可选",
            },
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler_name="design_plot",
    ),
    ToolDef(
        name="chapter_writer",
        description="生成章节正文。加载完整写作规则（行文/对话/去AI味/钩子/技法），将剧情设计和对白素材织成章节正文。创建章节前必须先调用此工具生成正文。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（必填）"},
            "requirements": {"type": "string", "description": "写作要求或方向（可选）"},
            "involved_characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本章出场的角色名列表",
            },
            "previous_plot": {
                "type": "object",
                "description": "design_plot 返回的剧情设计JSON（可选，如有则传入）",
            },
            "previous_roleplay": {
                "type": "array",
                "items": {"type": "object"},
                "description": "roleplay_character 或 dialogue_battle 返回的对白结果（可选，如有则传入）",
            },
            "mode": {
                "type": "string",
                "enum": ["fast", "quality"],
                "description": "写作模式。fast 使用精简直写提示词和更少外围轮次；quality 使用完整技法流程。两者都必须遵守角色、设定、时间线一致性和写后归档契约。默认由系统注入。",
            },
        },
        required=["outline_node_id"],
        tool_type="generator",
        estimated_cost="high",
        handler_name="chapter_writer",
    ),
    ToolDef(
        name="character_writer",
        description="生成角色卡片。加载完整角色设计规则（深度、一致性、反套路），根据项目上下文和用户要求创造出立体、有记忆点的角色。创建角色前必须先调用此工具生成角色卡片。",
        input_schema={
            "name": {"type": "string", "description": "角色名（可选，不传则由AI生成）"},
            "role_type": {
                "type": "string",
                "description": "建议角色类型：protagonist|supporting|antagonist|mentor|other",
            },
            "requirements": {"type": "string", "description": "用户对角色的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler_name="character_writer",
    ),
    ToolDef(
        name="outline_writer",
        description="生成大纲节点。加载故事结构规则，根据已有大纲、角色和世界观设计有因果推进和节奏变化的大纲节点。创建大纲前应先调用此工具生成大纲。",
        input_schema={
            "parent_id": {"type": "string", "description": "父节点ID（可选）"},
            "requirements": {"type": "string", "description": "用户对大纲的要求或方向（可选）"},
            "batch_count": {"type": "integer", "description": "生成节点数量，默认1，上限8"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler_name="outline_writer",
    ),
    ToolDef(
        name="worldbuilding_writer",
        description="生成世界观设定条目。加载维度专属设计规则（地理/历史/势力/规则体系/种族/文化），创造有深度、逻辑自洽、服务于剧情的世界观设定。创建世界观前应先调用此工具生成设定。",
        input_schema={
            "dimension": {
                "type": "string",
                "description": "维度：geography|history|factions|power_system|races|culture，默认culture",
            },
            "title": {"type": "string", "description": "建议标题（可选）"},
            "requirements": {"type": "string", "description": "用户对设定的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler_name="worldbuilding_writer",
    ),
    ToolDef(
        name="rewrite_text",
        description="按指定风格改写文本。自动修复禁用句式。用户说'改写''重写''润色'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要改写的原文"},
            "style": {
                "type": "string",
                "description": "目标风格：vivid|concise|serious|humorous|poetic，可选",
            },
            "prompt": {"type": "string", "description": "额外的改写要求，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler_name="rewrite_text",
    ),
    ToolDef(
        name="expand_text",
        description="扩充文本细节。自动修复禁用句式。用户说'扩写''丰富''展开'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要扩写的原文"},
            "prompt": {"type": "string", "description": "扩写方向提示，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler_name="expand_text",
    ),
    ToolDef(
        name="continue_text",
        description="从指定文本结尾处继续写作。自动修复禁用句式。用户说'续写''继续写'时使用。",
        input_schema={
            "text": {"type": "string", "description": "上文内容"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "prompt": {"type": "string", "description": "续写方向提示，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler_name="continue_text",
    ),
    ToolDef(
        name="roleplay_character",
        description="让单个角色对场景做出回应（对话/动作/内心独白）。AI扮演该角色，结果可直接用于章节正文。",
        input_schema={
            "character_id": {"type": "string", "description": "角色ID（优先使用）"},
            "character_name": {"type": "string", "description": "角色名（character_id为空时使用）"},
            "situation": {"type": "string", "description": "场景描述——告诉角色当前发生了什么"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        },
        required=["situation"],
        tool_type="generator",
        estimated_cost="medium",
        handler_name="roleplay_character",
    ),
    ToolDef(
        name="dialogue_battle",
        description="多个角色按回合制对戏。每个角色依次发言并承接上文，适用于需要自然对话的场景。",
        input_schema={
            "character_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参与对戏的角色名列表",
            },
            "character_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参与对戏的角色ID列表（与character_names二选一或互补）",
            },
            "scene": {"type": "string", "description": "场景描述——正在发生什么"},
            "turns": {"type": "integer", "description": "对戏回合数，默认2，最大4"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        },
        required=["scene"],
        tool_type="generator",
        estimated_cost="medium",
        handler_name="dialogue_battle",
    ),
    ToolDef(
        name="start_novel_creation_session",
        description="Start a new novel creation session. API-free. Returns interview checklist and prompt pack.",
        input_schema={
            "mode": {"type": "string", "description": "internal_llm|external_agent"},
            "user_brief": {"type": "string", "description": "User's novel brief"},
            "target_audience": {"type": "string", "description": "Target audience"},
            "genre": {"type": "string", "description": "Novel genre"},
            "platform": {"type": "string", "description": "Publishing platform"},
        },
        tool_type="read",
        estimated_cost="free",
        handler_name="start_novel_creation_session",
    ),
    ToolDef(
        name="draft_novel_blueprint",
        description="Draft novel blueprints for a creation session. Supports template, hybrid, internal_llm and external_agent modes.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "execution_mode": {
                "type": "string",
                "description": "template|hybrid|internal_llm|external_agent. hybrid uses template+LLM for creative output.",
            },
            "user_brief": {"type": "string", "description": "Additional user brief"},
            "feedback": {
                "type": "string",
                "description": "User feedback for refining or regenerating previous blueprint options",
            },
            "revision_mode": {
                "type": "string",
                "description": "initial|refine|regenerate. Use refine to adjust current direction, regenerate to restart options from feedback.",
            },
            "enhance_with_llm": {
                "type": "boolean",
                "description": "Optional slow LLM enhancement. Default false keeps template drafting instant.",
            },
            "skip_questions": {
                "type": "boolean",
                "description": "Skip clarifying questions and generate blueprints directly. Default false.",
            },
            "depth": {
                "type": "string",
                "description": "concept|full. Concept returns three lightweight cards and keeps full source inside the session.",
            },
        },
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="draft_novel_blueprint",
    ),
    ToolDef(
        name="review_novel_blueprint",
        description="Review novel blueprints. Supports hybrid, internal_llm and external_agent modes.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "execution_mode": {
                "type": "string",
                "description": "hybrid|internal_llm|external_agent",
            },
            "blueprint": {
                "type": "object",
                "description": "Blueprint to review (optional, saves to session)",
            },
        },
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="review_novel_blueprint",
    ),
    ToolDef(
        name="apply_novel_blueprint",
        description="Apply a confirmed blueprint to create a real Siming project with characters, worldbuilding, and outline.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "blueprint_index": {
                "type": "integer",
                "description": "Which blueprint to apply (default 0)",
            },
            "mode": {
                "type": "string",
                "description": "manual|auto. Manual returns candidates, auto creates project.",
            },
            "blueprint": {
                "type": "object",
                "description": "Optional blueprint override to apply directly.",
            },
        },
        required=["session_id"],
        tool_type="write",
        writes_project_data=True,
        risk_level="medium",
        estimated_cost="free",
        handler_name="apply_novel_blueprint",
    ),
    ToolDef(
        name="get_novel_creation_session",
        description="Read a resumable V2 novel creation session, its stage states, checkpoints, and recent runs.",
        input_schema={"session_id": {"type": "string", "description": "Creation session ID"}},
        required=["session_id"],
        tool_type="read",
        estimated_cost="free",
        handler_name="get_novel_creation_session",
    ),
    ToolDef(
        name="generate_novel_creation_stage",
        description="Generate one V2 creation stage or the complete quick pipeline. Saves only to the session draft; it never writes project files.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "stage": {
                "type": "string",
                "description": "constraints|concepts|world_style|characters|locations|macro_outline|opening_outline|final_review|all",
            },
            "model": {"type": "string", "description": "Optional model override for this stage"},
            "use_model": {
                "type": "boolean",
                "description": "Use the selected model to deepen the contract baseline",
            },
            "auto_confirm": {
                "type": "boolean",
                "description": "Confirm generated stages automatically; intended for quick mode",
            },
            "session_patch": {
                "type": "object",
                "description": "Optional editable form or selected concept update before generation",
            },
        },
        required=["session_id", "stage"],
        tool_type="write",
        writes_project_data=False,
        risk_level="low",
        estimated_cost="model_or_free",
        handler_name="generate_novel_creation_stage",
    ),
    ToolDef(
        name="submit_novel_creation_stage",
        description="Submit and optionally confirm an edited V2 creation stage. Changes remain in the session until apply_novel_blueprint.",
        input_schema={
            "session_id": {"type": "string", "description": "Creation session ID"},
            "stage": {"type": "string", "description": "Stage identifier"},
            "data": {"type": "object", "description": "Author or external-agent stage result"},
            "confirm": {"type": "boolean", "description": "Confirm this stage and continue"},
            "source": {"type": "string", "description": "author|local_cli|external_agent|model"},
        },
        required=["session_id", "stage", "data"],
        tool_type="write",
        writes_project_data=False,
        risk_level="low",
        estimated_cost="free",
        handler_name="submit_novel_creation_stage",
    ),
    ToolDef(
        name="list_imported_files",
        description="List all imported files in the working directory. Returns file names, paths, sizes, and modification times.",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler_name="list_imported_files",
    ),
    ToolDef(
        name="read_imported_file",
        description="Read the content of a specific imported file from the working directory.",
        input_schema={
            "filename": {
                "type": "string",
                "description": "Name of the file to read (from list_imported_files)",
            },
            "max_size": {
                "type": "integer",
                "description": "Max characters to read (default 50000)",
            },
        },
        required=["filename"],
        tool_type="read",
        estimated_cost="free",
        handler_name="read_imported_file",
    ),
)


__all__ = ["TOOL_DEFINITIONS"]
