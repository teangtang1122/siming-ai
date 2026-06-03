"""Central tool registry for workspace assistant.

Single source of truth for tool metadata, schemas, and handler bindings.
Adding a new tool requires only one change: register a ToolDef here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from .types import ToolHandler


# ---------------------------------------------------------------------------
# ToolDef — metadata for a single tool
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict  # JSON Schema "properties" dict
    required: list[str] = field(default_factory=list)
    tool_type: str = "read"  # read | write | analysis | generator | web | memory | scheduler
    idempotent: bool = False
    requires_confirmation: bool = False
    estimated_cost: str = "free"  # free | low | medium | high
    handler: ToolHandler | None = None


# ---------------------------------------------------------------------------
# ToolRegistry — manages all registered tools
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Central registry for workspace tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_handler(self, name: str) -> ToolHandler | None:
        td = self._tools.get(name)
        return td.handler if td else None

    def all_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(
        self,
        *,
        tool_types: set[str] | None = None,
        exclude_types: set[str] | None = None,
    ) -> list[dict]:
        """Return OpenAI function-calling format dicts, optionally filtered by type."""
        result: list[dict] = []
        for td in self._tools.values():
            if tool_types and td.tool_type not in tool_types:
                continue
            if exclude_types and td.tool_type in exclude_types:
                continue
            schema: dict = {"type": "object", "properties": td.input_schema}
            if td.required:
                schema["required"] = td.required
            result.append({
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": schema,
                },
            })
        return result

    def get_names_by_type(self, tool_type: str) -> set[str]:
        return {name for name, td in self._tools.items() if td.tool_type == tool_type}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Register all tools
# ---------------------------------------------------------------------------

def _register_all() -> None:
    from .tools import (
        chapter_writer,
        character_writer,
        continue_text,
        create_chapter,
        create_character,
        create_outline_node,
        create_relationship,
        create_worldbuilding_entry,
        delete_chapter,
        delete_character,
        delete_outline_node,
        delete_relationship,
        delete_worldbuilding_entry,
        design_plot,
        detect_character_changes,
        detect_forbidden_patterns,
        detect_new_worldbuilding,
        detect_worldbuilding_conflicts,
        dialogue_battle,
        evaluate_chapter,
        expand_text,
        forget,
        list_characters,
        list_memories,
        list_chapters,
        list_worldbuilding,
        outline_writer,
        recall,
        remember,
        rewrite_text,
        roleplay_character,
        search_characters,
        search_chapters,
        search_outline,
        search_outline_tree,
        search_relationships,
        search_worldbuilding,
        suggest_conflicts,
        update_chapter,
        update_character,
        update_outline_node,
        update_relationship,
        update_worldbuilding_entry,
        web_search,
        worldbuilding_writer,
        preview_writing_context,
    )
    from .tools.rag_tools import search_context, preview_rag_context, explain_context_selection

    _r = registry.register

    # ── Read: Search & Catalog ───────────────────────────────────────────

    _r(ToolDef(
        name="search_characters",
        description="按角色名片段搜索角色完整档案。返回角色姓名、外貌、性格、背景、能力列表、角色类型。内容截断至8000字。",
        input_schema={
            "query": {"type": "string", "description": "角色名片段，支持模糊匹配"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_characters,
    ))

    _r(ToolDef(
        name="search_chapters",
        description="搜索章节全文。按标题搜索，可选限定大纲节点。正文截断至8000字。",
        input_schema={
            "query": {"type": "string", "description": "章节标题片段，支持模糊匹配"},
            "outline_node_id": {"type": "string", "description": "限定大纲节点ID，传入后忽略query直接返回该节点下所有章节"},
            "limit": {"type": "integer", "description": "返回条数上限，默认5，最大20"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_chapters,
    ))

    _r(ToolDef(
        name="search_outline",
        description="按标题搜索大纲节点，或查看指定节点的子树。",
        input_schema={
            "query": {"type": "string", "description": "大纲标题片段，支持模糊匹配"},
            "node_id": {"type": "string", "description": "指定节点ID，传入后返回该节点及所有子孙节点（忽略query）"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大60"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_outline,
    ))

    _r(ToolDef(
        name="search_outline_tree",
        description="获取完整大纲树结构（仅标题和层级），或指定子树。",
        input_schema={
            "root_id": {"type": "string", "description": "可选，子树根节点ID。不传则返回完整大纲树"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_outline_tree,
    ))

    _r(ToolDef(
        name="search_worldbuilding",
        description="按标题搜索世界观条目完整内容。可按维度过滤。",
        input_schema={
            "query": {"type": "string", "description": "设定标题片段，支持模糊匹配"},
            "dimension": {"type": "string", "description": "限定维度：geography|history|factions|power_system|races|culture"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_worldbuilding,
    ))

    _r(ToolDef(
        name="search_relationships",
        description="查询角色的所有关系（与谁有关系、方向、关系类型、描述）。",
        input_schema={
            "character_id": {"type": "string", "description": "角色ID，优先使用"},
            "character_name": {"type": "string", "description": "角色名，character_id为空时使用"},
        },
        tool_type="read",
        estimated_cost="free",
        handler=search_relationships,
    ))

    _r(ToolDef(
        name="list_characters",
        description="快速概览全部角色（仅返回姓名、ID、角色类型）。轻量级，先调此工具确认角色是否存在，再决定是否需要 search_characters 查详情。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_characters,
    ))

    _r(ToolDef(
        name="list_chapters",
        description="快速概览全部章节（仅返回标题、ID、对应大纲节点ID）。轻量级，不含正文。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_chapters,
    ))

    _r(ToolDef(
        name="list_worldbuilding",
        description="快速概览全部世界观条目（仅返回标题、ID、维度）。轻量级，不含正文。",
        input_schema={},
        tool_type="read",
        estimated_cost="free",
        handler=list_worldbuilding,
    ))

    # ── Write: Worldbuilding CRUD ────────────────────────────────────────

    _r(ToolDef(
        name="create_worldbuilding_entry",
        description="创建一条新的世界观设定条目。",
        input_schema={
            "title": {"type": "string", "description": "条目标题"},
            "content": {"type": "string", "description": "条目正文内容"},
            "dimension": {"type": "string", "description": "所属维度：geography|history|factions|power_system|races|culture，默认culture"},
            "sort_order": {"type": "integer", "description": "排序序号"},
            "related_characters": {"type": "array", "items": {"type": "string"}, "description": "关联角色名列表"},
            "plot_usage": {"type": "string", "description": "剧情用途说明"},
            "constraints": {"type": "array", "items": {"type": "string"}, "description": "设定约束列表"},
        },
        required=["title", "content"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_worldbuilding_entry,
    ))

    _r(ToolDef(
        name="update_worldbuilding_entry",
        description="更新一条世界观条目。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "条目ID（优先使用）"},
            "title": {"type": "string", "description": "条目标题（id为空时用于定位；也可用于重命名）"},
            "dimension": {"type": "string", "description": "更新维度"},
            "content": {"type": "string", "description": "更新正文"},
            "sort_order": {"type": "integer", "description": "更新排序"},
        },
        required=["id"],
        tool_type="write",
        estimated_cost="free",
        handler=update_worldbuilding_entry,
    ))

    _r(ToolDef(
        name="delete_worldbuilding_entry",
        description="删除一条世界观条目。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "条目ID（优先使用）"},
            "title": {"type": "string", "description": "条目标题（id为空时使用）"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_worldbuilding_entry,
    ))

    # ── Write: Outline CRUD ──────────────────────────────────────────────

    _r(ToolDef(
        name="create_outline_node",
        description="创建新的大纲节点。",
        input_schema={
            "title": {"type": "string", "description": "节点标题"},
            "parent_id": {"type": "string", "description": "父节点ID，可空（作为根节点）"},
            "node_type": {"type": "string", "description": "节点类型：volume|chapter|section，默认chapter"},
            "summary": {"type": "string", "description": "本节点剧情摘要"},
            "status": {"type": "string", "description": "状态：pending|in_progress|completed，默认pending"},
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "本节点涉及的角色名列表"},
        },
        required=["title"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_outline_node,
    ))

    _r(ToolDef(
        name="update_outline_node",
        description="更新大纲节点。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "节点ID（优先使用）。也可用 title/outline_node_id/node_id/outline_node_title/current_title/old_title 定位"},
            "title": {"type": "string", "description": "更新标题"},
            "summary": {"type": "string", "description": "更新摘要"},
            "status": {"type": "string", "description": "更新状态：pending|in_progress|completed"},
            "node_type": {"type": "string", "description": "更新节点类型：volume|chapter|section"},
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "更新涉及的角色名列表（替换全部已有关联）"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_outline_node,
    ))

    _r(ToolDef(
        name="delete_outline_node",
        description="删除大纲节点（级联删除所有子节点）。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "节点ID（优先使用）。也可用 node_id/outline_node_id/title 定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_outline_node,
    ))

    # ── Write: Character CRUD ────────────────────────────────────────────

    _r(ToolDef(
        name="create_character",
        description="创建新角色，含完整人物卡片。",
        input_schema={
            "name": {"type": "string", "description": "角色名（必填，最长100字）"},
            "appearance": {"type": "string", "description": "外貌描写"},
            "personality": {"type": "string", "description": "性格特征"},
            "background": {"type": "string", "description": "背景故事"},
            "abilities": {"type": "array", "items": {"type": "string"}, "description": "能力/技能列表"},
            "role_type": {"type": "string", "description": "角色类型：protagonist|supporting|antagonist|mentor|other，默认supporting"},
            "speech_style": {"type": "string", "description": "说话风格，可合并进背景/AI提示词"},
            "motivation": {"type": "string", "description": "当前动机，可合并进背景/AI提示词"},
            "conflict": {"type": "string", "description": "核心冲突，可合并进背景/AI提示词"},
            "ai_config": {"type": "object", "description": "角色AI扮演配置，含 tone_style/catchphrases/verbosity/emotion_tendency/custom_system_prompt"},
            "custom_system_prompt": {"type": "string", "description": "角色AI扮演提示词，可直接存入角色AI配置"},
        },
        required=["name"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_character,
    ))

    _r(ToolDef(
        name="update_character",
        description="更新角色信息。用ID或角色名定位。只有传入的字段才会被更新。",
        input_schema={
            "id": {"type": "string", "description": "角色ID（优先使用）"},
            "name": {"type": "string", "description": "角色名（id为空时用于定位）"},
            "appearance": {"type": "string", "description": "更新外貌"},
            "personality": {"type": "string", "description": "更新性格"},
            "background": {"type": "string", "description": "更新背景"},
            "abilities": {"type": "array", "items": {"type": "string"}, "description": "更新能力列表（替换全部）"},
            "role_type": {"type": "string", "description": "更新角色类型：protagonist|supporting|antagonist|mentor|other"},
            "ai_config": {"type": "object", "description": "更新角色AI扮演配置，含 tone_style/catchphrases/verbosity/emotion_tendency/custom_system_prompt"},
            "custom_system_prompt": {"type": "string", "description": "更新角色AI扮演提示词"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_character,
    ))

    _r(ToolDef(
        name="delete_character",
        description="删除角色。用ID或角色名定位。",
        input_schema={
            "id": {"type": "string", "description": "角色ID（优先使用）"},
            "name": {"type": "string", "description": "角色名（id为空时使用）"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_character,
    ))

    # ── Write: Relationship CRUD ─────────────────────────────────────────

    _r(ToolDef(
        name="create_relationship",
        description="在两个角色之间创建关系。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（也可用 from 字段）"},
            "target": {"type": "string", "description": "角色B的名字或ID（也可用 to 字段）"},
            "relationship_type": {"type": "string", "description": "关系类型，如 父子/师徒/恋人/仇敌，默认'关联'"},
            "description": {"type": "string", "description": "关系描述"},
        },
        required=["source", "target"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_relationship,
    ))

    _r(ToolDef(
        name="update_relationship",
        description="更新两个角色之间的关系类型或描述。用source+target定位。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
            "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
            "relationship_type": {"type": "string", "description": "更新关系类型"},
            "description": {"type": "string", "description": "更新关系描述"},
        },
        required=["source", "target"],
        tool_type="write",
        estimated_cost="free",
        handler=update_relationship,
    ))

    _r(ToolDef(
        name="delete_relationship",
        description="删除两个角色之间的关系。用source+target定位。",
        input_schema={
            "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
            "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
        },
        required=["source", "target"],
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_relationship,
    ))

    # ── Write: Chapter CRUD ──────────────────────────────────────────────

    _r(ToolDef(
        name="create_chapter",
        description="创建新章节。正文将自动修复禁用句式。创建前须已有对应大纲节点。若正文来自chapter_writer，优先传draft_id/content_ref，避免复制长正文导致截断。",
        input_schema={
            "title": {"type": "string", "description": "章节标题"},
            "content": {"type": "string", "description": "章节正文，1800-2500字。内部换行用\\n。对白可自由使用引号。"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID。优先使用它保存完整正文，避免长正文在工具参数中截断。"},
            "content_ref": {"type": "string", "description": "同draft_id，chapter_writer返回的正文引用。"},
            "skip_style_repair": {"type": "boolean", "description": "是否跳过保存时禁用句式自动修复，默认false。"},
            "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（优先）。也可用 outline_node_title/outline_title"},
            "summary": {"type": "string", "description": "章节摘要，可选"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章出场的角色名列表"},
        },
        required=["title"],
        tool_type="write",
        idempotent=True,
        estimated_cost="free",
        handler=create_chapter,
    ))

    _r(ToolDef(
        name="update_chapter",
        description="更新章节。用ID或标题定位。正文将自动修复禁用句式。若正文来自chapter_writer，优先传draft_id/content_ref，避免复制长正文导致截断。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
            "title": {"type": "string", "description": "更新章节标题"},
            "content": {"type": "string", "description": "更新章节正文"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID。优先使用它保存完整正文，避免长正文在工具参数中截断。"},
            "content_ref": {"type": "string", "description": "同draft_id，chapter_writer返回的正文引用。"},
            "skip_style_repair": {"type": "boolean", "description": "是否跳过保存时禁用句式自动修复，默认false。"},
            "summary": {"type": "string", "description": "更新章节摘要"},
            "outline_node_id": {"type": "string", "description": "更新关联大纲节点"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "更新出场角色名列表（替换全部关联）"},
        },
        tool_type="write",
        estimated_cost="free",
        handler=update_chapter,
    ))

    _r(ToolDef(
        name="delete_chapter",
        description="删除章节。自动回退该章节中角色的状态变更。用ID或标题定位。",
        input_schema={
            "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title 定位"},
        },
        tool_type="write",
        requires_confirmation=True,
        estimated_cost="free",
        handler=delete_chapter,
    ))

    # ── Analysis ─────────────────────────────────────────────────────────

    _r(ToolDef(
        name="suggest_conflicts",
        description="基于当前剧情状态生成3种情节冲突建议（人物冲突/势力冲突/内心冲突）。用户说'设计冲突''加点矛盾'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "prompt": {"type": "string", "description": "用户倾向或额外上下文，可选"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=suggest_conflicts,
    ))

    _r(ToolDef(
        name="design_plot",
        description="设计完整章节剧情——含场景拆解、角色行为、冲突张力、情绪曲线、一致性检查等7个维度。用户说'设计剧情''这章怎么写'时使用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "出场的角色名或ID列表，可选"},
            "requirements": {"type": "string", "description": "用户的额外要求，可选"},
            "feedback": {"type": "string", "description": "对上一轮设计的反馈（迭代时使用），可选"},
            "previous_plot": {"type": "string", "description": "上一轮设计的剧情（迭代时使用），可选"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=design_plot,
    ))

    _r(ToolDef(
        name="detect_character_changes",
        description="检测章节中追踪角色的变化（技能/经历/关系/性格）。三种模式：1) 传draft_id/content_ref检测chapter_writer草稿；2) 传content+title检测未保存正文；3) 传chapter_id检测已保存章节（自动保存变化日志和时间线）。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（检测未保存的正文时使用，与title配合）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {"type": "string", "description": "已保存的章节ID（检测已保存章节时使用，会自动写入变化日志）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_character_changes,
    ))

    _r(ToolDef(
        name="detect_new_worldbuilding",
        description="检测章节正文中引入的新世界观设定——对照已有设定条目，找出正文中出现但尚未录入数据库的地点、规则、势力、种族、文化习俗等。只读不写，返回建议条目列表和原文参考。可传draft_id/content_ref或chapter_id，避免复制长正文。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（可选；优先用draft_id或chapter_id）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "chapter_id": {"type": "string", "description": "已保存章节ID，可替代content"},
            "title": {"type": "string", "description": "章节标题（可选）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_new_worldbuilding,
    ))

    _r(ToolDef(
        name="detect_worldbuilding_conflicts",
        description="检测全部世界观条目之间的逻辑矛盾、规则冲突、时间线不一致。",
        input_schema={},
        tool_type="analysis",
        estimated_cost="medium",
        handler=detect_worldbuilding_conflicts,
    ))

    _r(ToolDef(
        name="detect_forbidden_patterns",
        description="检测文本中的禁用句式（如'仿佛''不由得''很愤怒'等70+种AI高频套话）。纯规则匹配，不调LLM。",
        input_schema={
            "text": {"type": "string", "description": "要检测的文本"},
        },
        required=["text"],
        tool_type="analysis",
        estimated_cost="free",
        handler=detect_forbidden_patterns,
    ))

    _r(ToolDef(
        name="preview_writing_context",
        description="写作前上下文预检。显示本次章节写作将读取的大纲、近期摘要、角色当前状态、关系和世界观，并给出缺失/风险提示。质量模式创建或重写章节前应先调用。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {"type": "string", "description": "用户的写作方向或额外要求，可用于筛选世界观"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章预计出场的角色名或别名列表"},
            "recent_limit": {"type": "integer", "description": "读取最近章节摘要数量，默认5，最大12"},
            "character_limit": {"type": "integer", "description": "返回角色状态数量，默认8，最大16"},
            "worldbuilding_limit": {"type": "integer", "description": "返回世界观条目数量，默认16，最大32"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=preview_writing_context,
    ))

    # ── RAG Context Tools ───────────────────────────────────────────────

    _r(ToolDef(
        name="search_context",
        description="全文检索项目中所有已索引的内容（章节、大纲、角色、世界观、记忆等）。返回相关度排序的结果列表。适用于跨类型模糊搜索。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词，支持中英文"},
            "source_types": {"type": "array", "items": {"type": "string"}, "description": "限定搜索范围：chapter|chapter_summary|outline|character|character_timeline|worldbuilding|assistant_memory"},
            "limit": {"type": "integer", "description": "返回条数上限，默认20，最大50"},
        },
        required=["query"],
        tool_type="read",
        estimated_cost="free",
        handler=search_context,
    ))

    _r(ToolDef(
        name="preview_rag_context",
        description="预算感知的上下文打包预览。展示本次写作将使用的大纲、摘要、角色、世界观、记忆等上下文分区，每分区含选取原因、字符预算和相关性评分。与preview_writing_context不同，此工具使用RAG检索。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
            "budget_override": {"type": "object", "description": "预算覆盖：max_chapter_chars/max_summary_chars/max_character_chars/max_worldbuilding_chars/max_memory_chars/max_outline_chars/reserve_chars"},
            "pinned_chunk_ids": {"type": "array", "items": {"type": "string"}, "description": "固定选取的内容块ID列表，无论如何都会被包含"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=preview_rag_context,
    ))

    _r(ToolDef(
        name="explain_context_selection",
        description="解释为什么特定来源被选入或未选入上下文。传入来源ID列表，返回每个来源的评分详情和选取原因。用于理解上下文打包决策。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "目标大纲节点ID"},
            "source_ids": {"type": "array", "items": {"type": "string"}, "description": "要解释的来源ID列表"},
            "requirements": {"type": "string", "description": "写作方向或额外要求"},
        },
        required=["source_ids"],
        tool_type="analysis",
        estimated_cost="free",
        handler=explain_context_selection,
    ))

    _r(ToolDef(
        name="evaluate_chapter",
        description="对章节正文进行8维度80分评估（开头吸引力/情节推进/角色塑造/对话质量/悬念设置/节奏控制/展示性描写/语言质量）。传入draft_id/content_ref或content+title评估未保存正文，或传入chapter_id评估已保存章节。",
        input_schema={
            "content": {"type": "string", "description": "章节正文（评估未保存的正文时使用，与chapter_id二选一）"},
            "draft_id": {"type": "string", "description": "chapter_writer返回的草稿ID，可替代content"},
            "content_ref": {"type": "string", "description": "同draft_id"},
            "title": {"type": "string", "description": "章节标题（与content配合使用）"},
            "chapter_id": {"type": "string", "description": "已保存的章节ID（评估已保存的章节时使用）"},
        },
        tool_type="analysis",
        estimated_cost="medium",
        handler=evaluate_chapter,
    ))

    # ── Generator: LLM content generation ────────────────────────────────

    _r(ToolDef(
        name="chapter_writer",
        description="生成章节正文。加载完整写作规则（行文/对话/去AI味/钩子/技法），将剧情设计和对白素材织成章节正文。创建章节前必须先调用此工具生成正文。",
        input_schema={
            "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（必填）"},
            "requirements": {"type": "string", "description": "写作要求或方向（可选）"},
            "involved_characters": {"type": "array", "items": {"type": "string"}, "description": "本章出场的角色名列表"},
            "previous_plot": {"type": "object", "description": "design_plot 返回的剧情设计JSON（可选，如有则传入）"},
            "previous_roleplay": {"type": "array", "items": {"type": "object"}, "description": "roleplay_character 或 dialogue_battle 返回的对白结果（可选，如有则传入）"},
            "mode": {"type": "string", "enum": ["fast", "quality"], "description": "写作模式，fast=快速简洁（1500-2000字），quality=完整技法（1800-2500字）。默认由系统注入。"},
        },
        required=["outline_node_id"],
        tool_type="generator",
        estimated_cost="high",
        handler=chapter_writer,
    ))

    _r(ToolDef(
        name="character_writer",
        description="生成角色卡片。加载完整角色设计规则（深度、一致性、反套路），根据项目上下文和用户要求创造出立体、有记忆点的角色。创建角色前必须先调用此工具生成角色卡片。",
        input_schema={
            "name": {"type": "string", "description": "角色名（可选，不传则由AI生成）"},
            "role_type": {"type": "string", "description": "建议角色类型：protagonist|supporting|antagonist|mentor|other"},
            "requirements": {"type": "string", "description": "用户对角色的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=character_writer,
    ))

    _r(ToolDef(
        name="outline_writer",
        description="生成大纲节点。加载故事结构规则，根据已有大纲、角色和世界观设计有因果推进和节奏变化的大纲节点。创建大纲前应先调用此工具生成大纲。",
        input_schema={
            "parent_id": {"type": "string", "description": "父节点ID（可选）"},
            "requirements": {"type": "string", "description": "用户对大纲的要求或方向（可选）"},
            "batch_count": {"type": "integer", "description": "生成节点数量，默认1，上限8"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=outline_writer,
    ))

    _r(ToolDef(
        name="worldbuilding_writer",
        description="生成世界观设定条目。加载维度专属设计规则（地理/历史/势力/规则体系/种族/文化），创造有深度、逻辑自洽、服务于剧情的世界观设定。创建世界观前应先调用此工具生成设定。",
        input_schema={
            "dimension": {"type": "string", "description": "维度：geography|history|factions|power_system|races|culture，默认culture"},
            "title": {"type": "string", "description": "建议标题（可选）"},
            "requirements": {"type": "string", "description": "用户对设定的要求或方向（可选）"},
        },
        tool_type="generator",
        estimated_cost="high",
        handler=worldbuilding_writer,
    ))

    _r(ToolDef(
        name="rewrite_text",
        description="按指定风格改写文本。自动修复禁用句式。用户说'改写''重写''润色'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要改写的原文"},
            "style": {"type": "string", "description": "目标风格：vivid|concise|serious|humorous|poetic，可选"},
            "prompt": {"type": "string", "description": "额外的改写要求，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler=rewrite_text,
    ))

    _r(ToolDef(
        name="expand_text",
        description="扩充文本细节。自动修复禁用句式。用户说'扩写''丰富''展开'时使用。",
        input_schema={
            "text": {"type": "string", "description": "要扩写的原文"},
            "prompt": {"type": "string", "description": "扩写方向提示，可选"},
        },
        required=["text"],
        tool_type="generator",
        estimated_cost="low",
        handler=expand_text,
    ))

    _r(ToolDef(
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
        handler=continue_text,
    ))

    _r(ToolDef(
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
        handler=roleplay_character,
    ))

    _r(ToolDef(
        name="dialogue_battle",
        description="多个角色按回合制对戏。每个角色依次发言并承接上文，适用于需要自然对话的场景。",
        input_schema={
            "character_names": {"type": "array", "items": {"type": "string"}, "description": "参与对戏的角色名列表"},
            "character_ids": {"type": "array", "items": {"type": "string"}, "description": "参与对戏的角色ID列表（与character_names二选一或互补）"},
            "scene": {"type": "string", "description": "场景描述——正在发生什么"},
            "turns": {"type": "integer", "description": "对戏回合数，默认2，最大4"},
            "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        },
        required=["scene"],
        tool_type="generator",
        estimated_cost="medium",
        handler=dialogue_battle,
    ))

    # ── Web ──────────────────────────────────────────────────────────────

    _r(ToolDef(
        name="web_search",
        description="搜索互联网获取最新信息。适用于查证事实、获取参考资料（历史/地理/文化/科技等）。只读，可在任何阶段使用。",
        input_schema={
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认5，上限10"},
        },
        required=["query"],
        tool_type="web",
        estimated_cost="low",
        handler=web_search,
    ))

    # ── Memory ───────────────────────────────────────────────────────────

    _r(ToolDef(
        name="remember",
        description="保存一条持久化记忆。用户表达偏好或搜索到有用资料后使用。同key自动覆盖。回复中不要提及已保存。",
        input_schema={
            "key": {"type": "string", "description": "简短的记忆标识"},
            "value": {"type": "string", "description": "记忆内容"},
            "category": {"type": "string", "description": "分类：user_preference|project_fact|writing_style|research_note|workflow_preference，默认user_preference"},
            "importance": {"type": "integer", "description": "重要性0-10，默认5。≥7才会被优先召回"},
        },
        required=["key", "value"],
        tool_type="memory",
        estimated_cost="low",
        handler=remember,
    ))

    _r(ToolDef(
        name="recall",
        description="按关键词查询已保存的记忆。每次新对话开始时先查询相关记忆。",
        input_schema={
            "query": {"type": "string", "description": "搜索记忆的关键词"},
            "category": {"type": "string", "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference"},
            "limit": {"type": "integer", "description": "返回条数上限，默认10，最大20"},
        },
        tool_type="memory",
        estimated_cost="low",
        handler=recall,
    ))

    _r(ToolDef(
        name="forget",
        description="删除记忆。用户说'不要记住''忘掉'时使用。按ID或key定位。",
        input_schema={
            "id": {"type": "string", "description": "记忆记录ID（优先使用）"},
            "key": {"type": "string", "description": "记忆标识（id为空时使用，删除所有匹配key的记忆）"},
        },
        tool_type="memory",
        estimated_cost="low",
        handler=forget,
    ))

    _r(ToolDef(
        name="list_memories",
        description="列出已保存的记忆。可按分类筛选。用于浏览和管理记忆。",
        input_schema={
            "category": {"type": "string", "description": "可选分类过滤：user_preference|project_fact|writing_style|research_note|workflow_preference"},
            "limit": {"type": "integer", "description": "返回条数上限，默认30，最大100"},
        },
        tool_type="memory",
        estimated_cost="free",
        handler=list_memories,
    ))


_register_all()
