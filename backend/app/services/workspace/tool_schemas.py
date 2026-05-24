"""JSON Schema function definitions for all 39 workspace tools — OpenAI function-calling format.

Each tool is a dict compatible with OpenAI's `tools` parameter:
    {"type": "function", "function": {"name": str, "description": str, "parameters": {...}}}
"""

from __future__ import annotations


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    """Build a single function definition dict."""
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": schema,
        },
    }


# ── Search & Catalog tools ──────────────────────────────────────────────

SEARCH_CHARACTERS = _fn(
    "search_characters",
    "按角色名片段搜索角色完整档案。返回角色姓名、外貌、性格、背景、能力列表、角色类型。内容截断至8000字。",
    {
        "query": {"type": "string", "description": "角色名片段，支持模糊匹配"},
        "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
    },
    ["query"],
)

SEARCH_CHAPTERS = _fn(
    "search_chapters",
    "搜索章节全文。按标题搜索，可选限定大纲节点。正文截断至8000字。",
    {
        "query": {"type": "string", "description": "章节标题片段，支持模糊匹配"},
        "outline_node_id": {"type": "string", "description": "限定大纲节点ID，传入后忽略query直接返回该节点下所有章节"},
        "limit": {"type": "integer", "description": "返回条数上限，默认5，最大20"},
    },
)

SEARCH_OUTLINE = _fn(
    "search_outline",
    "按标题搜索大纲节点，或查看指定节点的子树。",
    {
        "query": {"type": "string", "description": "大纲标题片段，支持模糊匹配"},
        "node_id": {"type": "string", "description": "指定节点ID，传入后返回该节点及所有子孙节点（忽略query）"},
        "limit": {"type": "integer", "description": "返回条数上限，默认10，最大60"},
    },
)

SEARCH_OUTLINE_TREE = _fn(
    "search_outline_tree",
    "获取完整大纲树结构（仅标题和层级），或指定子树。",
    {
        "root_id": {"type": "string", "description": "可选，子树根节点ID。不传则返回完整大纲树"},
    },
)

SEARCH_WORLDBUILDING = _fn(
    "search_worldbuilding",
    "按标题搜索世界观条目完整内容。可按维度过滤。",
    {
        "query": {"type": "string", "description": "设定标题片段，支持模糊匹配"},
        "dimension": {"type": "string", "description": "限定维度：geography|history|factions|power_system|races|culture"},
        "limit": {"type": "integer", "description": "返回条数上限，默认10，最大30"},
    },
)

SEARCH_RELATIONSHIPS = _fn(
    "search_relationships",
    "查询角色的所有关系（与谁有关系、方向、关系类型、描述）。",
    {
        "character_id": {"type": "string", "description": "角色ID，优先使用"},
        "character_name": {"type": "string", "description": "角色名，character_id为空时使用"},
    },
)

LIST_CHARACTERS = _fn(
    "list_characters",
    "快速概览全部角色（仅返回姓名、ID、角色类型）。轻量级，先调此工具确认角色是否存在，再决定是否需要 search_characters 查详情。",
    {},
)

LIST_CHAPTERS = _fn(
    "list_chapters",
    "快速概览全部章节（仅返回标题、ID、对应大纲节点ID）。轻量级，不含正文。",
    {},
)

LIST_WORLDBUILDING = _fn(
    "list_worldbuilding",
    "快速概览全部世界观条目（仅返回标题、ID、维度）。轻量级，不含正文。",
    {},
)

# ── Worldbuilding tools ──────────────────────────────────────────────────

CREATE_WORLDBUILDING_ENTRY = _fn(
    "create_worldbuilding_entry",
    "创建一条新的世界观设定条目。",
    {
        "title": {"type": "string", "description": "条目标题"},
        "content": {"type": "string", "description": "条目正文内容"},
        "dimension": {"type": "string", "description": "所属维度：geography|history|factions|power_system|races|culture，默认culture"},
        "sort_order": {"type": "integer", "description": "排序序号"},
        "related_characters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "关联角色名列表",
        },
        "plot_usage": {"type": "string", "description": "剧情用途说明"},
        "constraints": {
            "type": "array",
            "items": {"type": "string"},
            "description": "设定约束列表",
        },
    },
    ["title", "content"],
)

UPDATE_WORLDBUILDING_ENTRY = _fn(
    "update_worldbuilding_entry",
    "更新一条世界观条目。用ID或标题定位。",
    {
        "id": {"type": "string", "description": "条目ID（优先使用）"},
        "title": {"type": "string", "description": "条目标题（id为空时用于定位；也可用于重命名）"},
        "dimension": {"type": "string", "description": "更新维度"},
        "content": {"type": "string", "description": "更新正文"},
        "sort_order": {"type": "integer", "description": "更新排序"},
    },
    ["id"],
)

DELETE_WORLDBUILDING_ENTRY = _fn(
    "delete_worldbuilding_entry",
    "删除一条世界观条目。用ID或标题定位。",
    {
        "id": {"type": "string", "description": "条目ID（优先使用）"},
        "title": {"type": "string", "description": "条目标题（id为空时使用）"},
    },
)

# ── Outline tools ────────────────────────────────────────────────────────

CREATE_OUTLINE_NODE = _fn(
    "create_outline_node",
    "创建新的大纲节点。",
    {
        "title": {"type": "string", "description": "节点标题"},
        "parent_id": {"type": "string", "description": "父节点ID，可空（作为根节点）"},
        "node_type": {"type": "string", "description": "节点类型：volume|chapter|section，默认chapter"},
        "summary": {"type": "string", "description": "本节点剧情摘要"},
        "status": {"type": "string", "description": "状态：pending|in_progress|completed，默认pending"},
        "character_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "本节点涉及的角色名列表",
        },
    },
    ["title"],
)

UPDATE_OUTLINE_NODE = _fn(
    "update_outline_node",
    "更新大纲节点。用ID或标题定位。",
    {
        "id": {"type": "string", "description": "节点ID（优先使用）。也可用 title/outline_node_id/node_id/outline_node_title/current_title/old_title 定位"},
        "title": {"type": "string", "description": "更新标题"},
        "summary": {"type": "string", "description": "更新摘要"},
        "status": {"type": "string", "description": "更新状态：pending|in_progress|completed"},
        "node_type": {"type": "string", "description": "更新节点类型：volume|chapter|section"},
        "character_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "更新涉及的角色名列表（替换全部已有关联）",
        },
    },
)

DELETE_OUTLINE_NODE = _fn(
    "delete_outline_node",
    "删除大纲节点（级联删除所有子节点）。用ID或标题定位。",
    {
        "id": {"type": "string", "description": "节点ID（优先使用）。也可用 node_id/outline_node_id/title 定位"},
    },
)

# ── Character tools ──────────────────────────────────────────────────────

CREATE_CHARACTER = _fn(
    "create_character",
    "创建新角色，含完整人物卡片。",
    {
        "name": {"type": "string", "description": "角色名（必填，最长100字）"},
        "appearance": {"type": "string", "description": "外貌描写"},
        "personality": {"type": "string", "description": "性格特征"},
        "background": {"type": "string", "description": "背景故事"},
        "abilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "能力/技能列表",
        },
        "role_type": {"type": "string", "description": "角色类型：protagonist|supporting|antagonist|mentor|other，默认supporting"},
    },
    ["name"],
)

UPDATE_CHARACTER = _fn(
    "update_character",
    "更新角色信息。用ID或角色名定位。只有传入的字段才会被更新。",
    {
        "id": {"type": "string", "description": "角色ID（优先使用）"},
        "name": {"type": "string", "description": "角色名（id为空时用于定位）"},
        "appearance": {"type": "string", "description": "更新外貌"},
        "personality": {"type": "string", "description": "更新性格"},
        "background": {"type": "string", "description": "更新背景"},
        "abilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "更新能力列表（替换全部）",
        },
        "role_type": {"type": "string", "description": "更新角色类型：protagonist|supporting|antagonist|mentor|other"},
    },
)

DELETE_CHARACTER = _fn(
    "delete_character",
    "删除角色。用ID或角色名定位。",
    {
        "id": {"type": "string", "description": "角色ID（优先使用）"},
        "name": {"type": "string", "description": "角色名（id为空时使用）"},
    },
)

# ── Relationship tools ───────────────────────────────────────────────────

CREATE_RELATIONSHIP = _fn(
    "create_relationship",
    "在两个角色之间创建关系。",
    {
        "source": {"type": "string", "description": "角色A的名字或ID（也可用 from 字段）"},
        "target": {"type": "string", "description": "角色B的名字或ID（也可用 to 字段）"},
        "relationship_type": {"type": "string", "description": "关系类型，如 父子/师徒/恋人/仇敌，默认'关联'"},
        "description": {"type": "string", "description": "关系描述"},
    },
    ["source", "target"],
)

UPDATE_RELATIONSHIP = _fn(
    "update_relationship",
    "更新两个角色之间的关系类型或描述。用source+target定位。",
    {
        "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
        "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
        "relationship_type": {"type": "string", "description": "更新关系类型"},
        "description": {"type": "string", "description": "更新关系描述"},
    },
    ["source", "target"],
)

DELETE_RELATIONSHIP = _fn(
    "delete_relationship",
    "删除两个角色之间的关系。用source+target定位。",
    {
        "source": {"type": "string", "description": "角色A的名字或ID（必填，也可用 from）"},
        "target": {"type": "string", "description": "角色B的名字或ID（必填，也可用 to）"},
    },
    ["source", "target"],
)

# ── Chapter tools ────────────────────────────────────────────────────────

CREATE_CHAPTER = _fn(
    "create_chapter",
    "创建新章节。正文将自动修复禁用句式。创建前须已有对应大纲节点。",
    {
        "title": {"type": "string", "description": "章节标题"},
        "content": {"type": "string", "description": "章节正文，1800-2500字。内部换行用\\n。对白可自由使用引号。"},
        "outline_node_id": {"type": "string", "description": "对应的大纲节点ID（优先）。也可用 outline_node_title/outline_title"},
        "summary": {"type": "string", "description": "章节摘要，可选"},
        "involved_characters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "本章出场的角色名列表",
        },
    },
    ["title", "content"],
)

UPDATE_CHAPTER = _fn(
    "update_chapter",
    "更新章节。用ID或标题定位。正文将自动修复禁用句式。",
    {
        "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title/outline_node_id 定位"},
        "title": {"type": "string", "description": "更新章节标题"},
        "content": {"type": "string", "description": "更新章节正文"},
        "summary": {"type": "string", "description": "更新章节摘要"},
        "outline_node_id": {"type": "string", "description": "更新关联大纲节点"},
        "involved_characters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "更新出场角色名列表（替换全部关联）",
        },
    },
)

DELETE_CHAPTER = _fn(
    "delete_chapter",
    "删除章节。自动回退该章节中角色的状态变更。用ID或标题定位。",
    {
        "id": {"type": "string", "description": "章节ID（优先使用）。也可用 chapter_id/title/chapter_title 定位"},
    },
)

# ── Roleplay tools ───────────────────────────────────────────────────────

ROLEPLAY_CHARACTER = _fn(
    "roleplay_character",
    "让单个角色对场景做出回应（对话/动作/内心独白）。AI扮演该角色，结果可直接用于章节正文。",
    {
        "character_id": {"type": "string", "description": "角色ID（优先使用）"},
        "character_name": {"type": "string", "description": "角色名（character_id为空时使用）"},
        "situation": {"type": "string", "description": "场景描述——告诉角色当前发生了什么"},
        "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
    },
    ["situation"],
)

DIALOGUE_BATTLE = _fn(
    "dialogue_battle",
    "多个角色按回合制对戏。每个角色依次发言并承接上文，适用于需要自然对话的场景。",
    {
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
    ["scene"],
)

# ── Text operation tools ─────────────────────────────────────────────────

REWRITE_TEXT = _fn(
    "rewrite_text",
    "按指定风格改写文本。自动修复禁用句式。用户说'改写''重写''润色'时使用。",
    {
        "text": {"type": "string", "description": "要改写的原文"},
        "style": {"type": "string", "description": "目标风格：vivid|concise|serious|humorous|poetic，可选"},
        "prompt": {"type": "string", "description": "额外的改写要求，可选"},
    },
    ["text"],
)

EXPAND_TEXT = _fn(
    "expand_text",
    "扩充文本细节。自动修复禁用句式。用户说'扩写''丰富''展开'时使用。",
    {
        "text": {"type": "string", "description": "要扩写的原文"},
        "prompt": {"type": "string", "description": "扩写方向提示，可选"},
    },
    ["text"],
)

CONTINUE_TEXT = _fn(
    "continue_text",
    "从指定文本结尾处继续写作。自动修复禁用句式。用户说'续写''继续写'时使用。",
    {
        "text": {"type": "string", "description": "上文内容"},
        "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        "prompt": {"type": "string", "description": "续写方向提示，可选"},
    },
    ["text"],
)

# ── Analysis tools ───────────────────────────────────────────────────────

SUGGEST_CONFLICTS = _fn(
    "suggest_conflicts",
    "基于当前剧情状态生成3种情节冲突建议（人物冲突/势力冲突/内心冲突）。用户说'设计冲突''加点矛盾'时使用。",
    {
        "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        "prompt": {"type": "string", "description": "用户倾向或额外上下文，可选"},
    },
)

DESIGN_PLOT = _fn(
    "design_plot",
    "设计完整章节剧情——含场景拆解、角色行为、冲突张力、情绪曲线、一致性检查等7个维度。用户说'设计剧情''这章怎么写'时使用。",
    {
        "outline_node_id": {"type": "string", "description": "关联的大纲节点ID，可选"},
        "involved_characters": {
            "type": "array",
            "items": {"type": "string"},
            "description": "出场的角色名或ID列表，可选",
        },
        "requirements": {"type": "string", "description": "用户的额外要求，可选"},
        "feedback": {"type": "string", "description": "对上一轮设计的反馈（迭代时使用），可选"},
        "previous_plot": {"type": "string", "description": "上一轮设计的剧情（迭代时使用），可选"},
    },
)

DETECT_CHARACTER_CHANGES = _fn(
    "detect_character_changes",
    "检测章节中追踪角色的变化（技能/经历/关系/性格）。两种模式：1) 传content+title检测未保存的正文（只返回变化，不写库）；2) 传chapter_id检测已保存的章节（自动保存变化日志和时间线）。",
    {
        "content": {"type": "string", "description": "章节正文（检测未保存的正文时使用，与title配合）"},
        "title": {"type": "string", "description": "章节标题（与content配合使用）"},
        "chapter_id": {"type": "string", "description": "已保存的章节ID（检测已保存章节时使用，会自动写入变化日志）"},
    },
)

DETECT_NEW_WORLDBUILDING = _fn(
    "detect_new_worldbuilding",
    "检测章节正文中引入的新世界观设定——对照已有设定条目，找出正文中出现但尚未录入数据库的地点、规则、势力、种族、文化习俗等。只读不写，返回建议条目列表和原文参考。创建章节前，在 evaluate_chapter 通过后调用此工具。",
    {
        "content": {"type": "string", "description": "章节正文（必填）"},
        "title": {"type": "string", "description": "章节标题（可选）"},
    },
    ["content"],
)

DETECT_WORLDBUILDING_CONFLICTS = _fn(
    "detect_worldbuilding_conflicts",
    "检测全部世界观条目之间的逻辑矛盾、规则冲突、时间线不一致。",
    {},
)

DETECT_FORBIDDEN_PATTERNS = _fn(
    "detect_forbidden_patterns",
    "检测文本中的禁用句式（如'仿佛''不由得''很愤怒'等70+种AI高频套话）。纯规则匹配，不调LLM。",
    {
        "text": {"type": "string", "description": "要检测的文本"},
    },
    ["text"],
)

CHAPTER_WRITER = _fn(
    "chapter_writer",
    "生成章节正文。加载完整写作规则（行文/对话/去AI味/钩子/技法），将剧情设计和对白素材织成章节正文。创建章节前必须先调用此工具生成正文。",
    {
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
    },
    ["outline_node_id"],
)

EVALUATE_CHAPTER = _fn(
    "evaluate_chapter",
    "对章节正文进行8维度80分评估（开头吸引力/情节推进/角色塑造/对话质量/悬念设置/节奏控制/展示性描写/语言质量）。传入content+title评估未保存的正文，或传入chapter_id评估已保存的章节。",
    {
        "content": {"type": "string", "description": "章节正文（评估未保存的正文时使用，与chapter_id二选一）"},
        "title": {"type": "string", "description": "章节标题（与content配合使用）"},
        "chapter_id": {"type": "string", "description": "已保存的章节ID（评估已保存的章节时使用）"},
    },
)

# ── Writer tools (character / outline / worldbuilding) ────────────────────

CHARACTER_WRITER = _fn(
    "character_writer",
    "生成角色卡片。加载完整角色设计规则（深度、一致性、反套路），根据项目上下文和用户要求创造出立体、有记忆点的角色。创建角色前必须先调用此工具生成角色卡片。",
    {
        "name": {"type": "string", "description": "角色名（可选，不传则由AI生成）"},
        "role_type": {"type": "string", "description": "建议角色类型：protagonist|supporting|antagonist|mentor|other"},
        "requirements": {"type": "string", "description": "用户对角色的要求或方向（可选）"},
    },
)

OUTLINE_WRITER = _fn(
    "outline_writer",
    "生成大纲节点。加载故事结构规则，根据已有大纲、角色和世界观设计有因果推进和节奏变化的大纲节点。创建大纲前应先调用此工具生成大纲。",
    {
        "parent_id": {"type": "string", "description": "父节点ID（可选）"},
        "requirements": {"type": "string", "description": "用户对大纲的要求或方向（可选）"},
        "batch_count": {"type": "integer", "description": "生成节点数量，默认1，上限8"},
    },
)

WORLDBUILDING_WRITER = _fn(
    "worldbuilding_writer",
    "生成世界观设定条目。加载维度专属设计规则（地理/历史/势力/规则体系/种族/文化），创造有深度、逻辑自洽、服务于剧情的世界观设定。创建世界观前应先调用此工具生成设定。",
    {
        "dimension": {"type": "string", "description": "维度：geography|history|factions|power_system|races|culture，默认culture"},
        "title": {"type": "string", "description": "建议标题（可选）"},
        "requirements": {"type": "string", "description": "用户对设定的要求或方向（可选）"},
    },
)

# ── Web search tool ──────────────────────────────────────────────────────

WEB_SEARCH = _fn(
    "web_search",
    "搜索互联网获取最新信息。适用于查证事实、获取参考资料（历史/地理/文化/科技等）。只读，可在任何阶段使用。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
        "max_results": {"type": "integer", "description": "最大结果数，默认5，上限10"},
    },
    ["query"],
)

# ── Memory tools ─────────────────────────────────────────────────────────

REMEMBER = _fn(
    "remember",
    "保存一条持久化记忆。用户表达偏好或搜索到有用资料后使用。同key自动覆盖。回复中不要提及已保存。",
    {
        "key": {"type": "string", "description": "简短的记忆标识"},
        "value": {"type": "string", "description": "记忆内容"},
        "category": {"type": "string", "description": "分类：preference|search_result|note|fact，默认note"},
        "importance": {"type": "integer", "description": "重要性0-10，默认5。≥7才会被优先召回"},
    },
    ["key", "value"],
)

RECALL = _fn(
    "recall",
    "按关键词查询已保存的记忆。每次新对话开始时先查询相关记忆。",
    {
        "query": {"type": "string", "description": "搜索记忆的关键词"},
        "category": {"type": "string", "description": "可选分类过滤：preference|search_result|note|fact"},
        "limit": {"type": "integer", "description": "返回条数上限，默认10，最大20"},
    },
)

FORGET = _fn(
    "forget",
    "删除记忆。用户说'不要记住''忘掉'时使用。按ID或key定位。",
    {
        "id": {"type": "string", "description": "记忆记录ID（优先使用）"},
        "key": {"type": "string", "description": "记忆标识（id为空时使用，删除所有匹配key的记忆）"},
    },
)

# ── Aggregated lists ─────────────────────────────────────────────────────

# Search/read/analyze tools — allowed during information-gathering rounds
SEARCH_TOOL_SCHEMAS: list[dict] = [
    LIST_CHARACTERS,
    LIST_CHAPTERS,
    LIST_WORLDBUILDING,
    SEARCH_CHARACTERS,
    SEARCH_CHAPTERS,
    SEARCH_OUTLINE,
    SEARCH_OUTLINE_TREE,
    SEARCH_WORLDBUILDING,
    SEARCH_RELATIONSHIPS,
    DESIGN_PLOT,
    SUGGEST_CONFLICTS,
    ROLEPLAY_CHARACTER,
    DIALOGUE_BATTLE,
    DETECT_WORLDBUILDING_CONFLICTS,
    DETECT_FORBIDDEN_PATTERNS,
    REWRITE_TEXT,
    EXPAND_TEXT,
    CONTINUE_TEXT,
    CHAPTER_WRITER,
    EVALUATE_CHAPTER,
    CHARACTER_WRITER,
    OUTLINE_WRITER,
    WORLDBUILDING_WRITER,
    DETECT_CHARACTER_CHANGES,
    DETECT_NEW_WORLDBUILDING,
    WEB_SEARCH,
    RECALL,
    REMEMBER,
    FORGET,
]

# Write tools — only allowed when the assistant is ready to commit changes
WRITE_TOOL_SCHEMAS: list[dict] = [
    CREATE_WORLDBUILDING_ENTRY,
    UPDATE_WORLDBUILDING_ENTRY,
    DELETE_WORLDBUILDING_ENTRY,
    CREATE_OUTLINE_NODE,
    UPDATE_OUTLINE_NODE,
    DELETE_OUTLINE_NODE,
    CREATE_CHARACTER,
    UPDATE_CHARACTER,
    DELETE_CHARACTER,
    CREATE_RELATIONSHIP,
    UPDATE_RELATIONSHIP,
    DELETE_RELATIONSHIP,
    CREATE_CHAPTER,
    UPDATE_CHAPTER,
    DELETE_CHAPTER,
]

ALL_TOOL_SCHEMAS: list[dict] = SEARCH_TOOL_SCHEMAS + WRITE_TOOL_SCHEMAS

# Search-tool names for quick classification
SEARCH_TOOL_NAMES: set[str] = {s["function"]["name"] for s in SEARCH_TOOL_SCHEMAS}
WRITE_TOOL_NAMES: set[str] = {s["function"]["name"] for s in WRITE_TOOL_SCHEMAS}


def build_tool_schemas(*, search_only: bool = False) -> list[dict]:
    """Return the appropriate tool schema list.

    Args:
        search_only: If True, return only search/read tools (for info-gathering rounds).
                     If False, return all tools.

    Used by the agentic loop to expose different tools at different phases.
    """
    if search_only:
        return list(SEARCH_TOOL_SCHEMAS)
    return list(ALL_TOOL_SCHEMAS)
