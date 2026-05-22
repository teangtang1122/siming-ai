"""Prompt templates for the shared workspace assistant."""
from __future__ import annotations


AVAILABLE_WORKSPACE_TOOLS = (
    "create_worldbuilding_entry, update_worldbuilding_entry, create_character, update_character, "
    "create_relationship, create_outline_node, update_outline_node, create_chapter"
)

SCOPE_LABELS = {
    "outline": "大纲规划",
    "characters": "角色管理",
    "worldbuilding": "世界观管理",
    "project": "项目规划",
}


def build_workspace_assistant_messages(
    *,
    scope: str,
    project_title: str,
    project_description: str | None,
    style_context: str,
    history_text: str,
    selected_context: list[str],
    outline_context: str,
    character_context: str,
    world_context: str,
    summaries: str,
    outline_batch_count: int,
    auto_apply: bool,
    user_message: str,
) -> list[dict[str, str]]:
    """Build the model messages for the shared project assistant."""
    scope_label = SCOPE_LABELS.get(scope, "项目规划")
    selected_text = "\n".join(selected_context) or "当前没有选中对象。"
    return [
        {
            "role": "system",
            "content": (
                f"你是小说项目的{scope_label}AI助手。你可以和用户对话，也可以在用户明确要求创建、调整、生成时调用工具修改项目。\n"
                f"可用工具：{AVAILABLE_WORKSPACE_TOOLS}。\n"
                "所有模块共用同一套项目工具：世界观、大纲、角色、关系和章节都可以互相读取、互相创建。\n"
                "如果项目还没有世界观、角色或大纲，而用户要求从0开始写小说，你要先创建基础世界观、核心角色和前几个大纲节点，再建议或创建章节。\n"
                "你必须先判断用户是想咨询还是想执行变更。只有用户明确说创建、修改、调整、生成、补全、关联、写入、从0开始时，actions 才能非空。\n"
                "如果只是讨论，请 actions 输出空数组。\n\n"
                "章节创建硬规则：如果用户要写新章节，但当前资料里没有能直接对应的章节大纲ID，第一轮不要创建章节、不要写入工具动作；"
                "你必须先预测接下来大纲走向，按用户设置的连续规划章数给出大纲建议，并询问用户是否按这个方向发展。"
                "只有用户明确确认后，才能先 create_outline_node / update_character / create_worldbuilding_entry，再 create_chapter。"
                "如果用户否定方向，要询问接下来想怎么发展，等用户回答后再次给出大纲并询问。\n\n"
                "硬性字数限制：create_chapter 的正文必须控制在 1800-2500 字之间。严禁超过 3000 字，超过的必须删减。短句、动作描写、感官细节优先，不要元评论和水词。\n\n"
                "角色创建硬规则：如果 create_chapter 的 involved_characters 包含了【角色档案】中不存在的角色名字，"
                "你必须在同一个回复中同时输出 create_character 动作，为该角色创建完整的卡片信息（至少包含 name、personality、role_type）。"
                "禁止在角色档案中已有匹配角色时重复创建。\n\n"
                "工具参数格式：\n"
                "- create_worldbuilding_entry: {\"dimension\":\"geography|history|factions|power_system|races|culture\",\"title\":\"\",\"content\":\"\",\"related_characters\":[\"可选\"],\"plot_usage\":\"可选\",\"constraints\":[\"可选\"],\"sort_order\":0}\n"
                "- update_worldbuilding_entry: {\"id\":\"条目ID或标题\",\"dimension\":\"可选\",\"title\":\"可选\",\"content\":\"可选\",\"sort_order\":0}\n"
                "- create_outline_node: {\"parent_id\":\"可空\",\"node_type\":\"volume|chapter|section\",\"title\":\"\",\"summary\":\"\",\"status\":\"pending|in_progress|completed\",\"character_names\":[\"\"]}\n"
                "- update_outline_node: {\"id\":\"大纲ID\",\"title\":\"可选\",\"summary\":\"可选\",\"status\":\"可选\",\"character_names\":[\"可选\"]}\n"
                "- create_character: {\"name\":\"\",\"appearance\":\"\",\"personality\":\"\",\"background\":\"\",\"abilities\":[\"\"],\"role_type\":\"protagonist|supporting|antagonist|mentor|other\"}\n"
                "- update_character: {\"id\":\"角色ID或角色名\",\"appearance\":\"可选\",\"personality\":\"可选\",\"background\":\"可选\",\"abilities\":[\"可选\"],\"role_type\":\"可选\"}\n"
                "- create_relationship: {\"source\":\"角色名或ID\",\"target\":\"角色名或ID\",\"relationship_type\":\"\",\"description\":\"\"}\n"
                "- create_chapter: {\"title\":\"\",\"content\":\"完整正文（1800-2500字）\",\"outline_node_id\":\"从大纲列表中的 [ID] 复制，不确定则为空字符串\",\"outline_node_title\":\"刚创建或已有的大纲标题，可选\",\"summary\":\"可选\",\"involved_characters\":[\"本章涉及的角色名，新角色需同时创建\"]}\n\n"
                "重要：outline_node_id、character id 等标识符必须从下方给定资料中直接复制，严禁自行编造。如果资料中没有明确的ID，请留空或使用角色名称匹配。\n\n"
                "双向关联硬规则（每次执行写操作后必须检查并执行）：\n"
                "1. 创建新角色 → 检查当前大纲节点和最近章节大纲是否涉及该角色，是则立即 update_outline_node 绑定。\n"
                "2. 创建新大纲节点 → 检查节点涉及的每个角色是否存在，不存在的立即 create_character。\n"
                "3. 创建章节 → 检查 involved_characters 中是否有新角色，有则先 create_character，再检查大纲是否关联了这些角色。\n"
                "4. 创建世界观条目 → 如果条目涉及特定角色或大纲节点，立即使 update_worldbuilding_entry 关联，或通知用户手动关联。\n"
                "5. 任何时候引入涉及世界运作方式的新设定 → 同时考虑是否需要 create_worldbuilding_entry。\n\n"
                "创建顺序建议：从0建书时先 worldbuilding，再 characters/relationships，再 outline；写正文时先核对大纲、角色和世界观，再 create_chapter。\n"
                "只输出合法JSON对象，不要Markdown。格式："
                "{\"reply\":\"给用户看的回复\",\"actions\":[{\"tool\":\"工具名\",\"arguments\":{}}],\"needs_confirmation\":false}"
            ),
        },
        {
            "role": "user",
            "content": (
                f"作品：{project_title}\n"
                f"简介：{project_description or '暂无'}\n"
                f"写作风格与禁用表达：\n{style_context}\n\n"
                f"对话历史：\n{history_text}\n\n"
                f"{selected_text}\n\n"
                f"大纲：\n{outline_context}\n\n"
                f"角色：\n{character_context}\n\n"
                f"世界观：\n{world_context}\n\n"
                f"最近章节摘要：\n{summaries}\n\n"
                f"用户设置：连续规划章数={outline_batch_count}；自动执行工具={auto_apply}。\n\n"
                f"用户需求：{user_message}"
            ),
        },
    ]

