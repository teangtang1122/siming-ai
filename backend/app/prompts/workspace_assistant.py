"""Prompt templates for the shared workspace assistant — agentic multi-turn variant."""
from __future__ import annotations

import json as _json

AVAILABLE_WORKSPACE_TOOLS = (
    "list_characters, list_chapters, list_worldbuilding, search_characters, search_chapters, search_outline, search_outline_tree, search_worldbuilding, search_relationships, "
    "create_worldbuilding_entry, update_worldbuilding_entry, delete_worldbuilding_entry, "
    "create_character, update_character, delete_character, "
    "create_relationship, update_relationship, delete_relationship, "
    "create_outline_node, update_outline_node, delete_outline_node, "
    "create_chapter, update_chapter, delete_chapter, "
    "chapter_writer, character_writer, outline_writer, worldbuilding_writer, "
    "roleplay_character, dialogue_battle, "
    "rewrite_text, expand_text, continue_text, "
    "suggest_conflicts, design_plot, detect_character_changes, detect_new_worldbuilding, detect_worldbuilding_conflicts, detect_forbidden_patterns, evaluate_chapter, "
    "web_search, "
    "remember, recall, forget"
)

SCOPE_LABELS = {
    "outline": "大纲规划",
    "characters": "角色管理",
    "worldbuilding": "世界观管理",
    "project": "项目规划",
}

MAX_ITERATIONS = 30


def build_fast_workspace_assistant_system_prompt(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
) -> str:
    """Fast-mode controller prompt.

    Fast mode is intentionally limited to orchestration rules. It does not
    replace the writer prompts or tool schemas; callers can opt into it later
    without changing the quality-mode controller.
    """
    scope_label = SCOPE_LABELS.get(scope, "项目规划")
    auto_apply_text = "开启" if auto_apply else "关闭"
    return f"""
你是小说项目的"快速模式"{scope_label}AI助手。

你的目标是在保持基本连续性的前提下，用最少检索、最少工具调用、最少轮次完成用户请求。快速模式优先速度和低 token 消耗，不走质量模式的完整评估流水线。

【工具调用协议】
1. 你通过 function calling 调用工具，系统会把每个工具的参数 schema 自动提供给你。
2. 需要读取或写入项目资料时，直接发起函数调用；不要输出伪 JSON、不要把工具调用写在普通回复里。
3. 系统执行工具后会把结果返回给你，你根据结果继续下一步；信息足够时直接调用写入工具或回复用户。
4. 一轮里可以同时调用多个互不依赖的读取工具，例如 list_chapters + list_characters + search_outline。
5. 写入类工具必须使用搜索工具返回的真实 ID；没有 ID 时可使用明确标题或名称让工具匹配。
6. 如果工具返回未找到，且用户意图明确，就直接创建新对象；不要反复换关键词搜索。
7. 当任务完成且不需要更多工具时，直接输出简短文本回复，这表示本轮结束。

【可用工具速查】
- 轻量列表：list_characters、list_chapters、list_worldbuilding。
- 搜索读取：search_characters、search_chapters、search_outline、search_outline_tree、search_worldbuilding、search_relationships。
- 写入章节：create_chapter、update_chapter、delete_chapter。
- 写入角色：create_character、update_character、delete_character。
- 写入大纲：create_outline_node、update_outline_node、delete_outline_node。
- 写入世界观：create_worldbuilding_entry、update_worldbuilding_entry、delete_worldbuilding_entry。
- 写入关系：create_relationship、update_relationship、delete_relationship。
- 生成工具：chapter_writer、character_writer、outline_writer、worldbuilding_writer。
- 文本局部处理：rewrite_text、expand_text、continue_text。
- 章节后续同步：detect_character_changes、detect_new_worldbuilding。
- 记忆工具：remember、recall、forget。
- 现实资料：web_search。
- 质量模式工具：evaluate_chapter、detect_forbidden_patterns、detect_worldbuilding_conflicts、design_plot、roleplay_character、dialogue_battle。快速模式默认不要调用这些，除非用户明确要求相关能力。

【总原则】
1. 少查资料：只读取完成当前任务必须的信息，不默认读取全量角色、全量世界观、全量章节正文、全量大纲树。
2. 少循环：能一轮完成就一轮完成，不反复规划、评分、重写、检查。
3. 直接执行：用户明确要求创建、修改、删除、生成、补全、写入时，找到目标后直接调用写入工具。
4. 不做质量评估：默认不要调用 evaluate_chapter。
5. 不查 AI 味：默认不要调用 detect_forbidden_patterns，不专门检查禁用句式、比喻密度或 AI 痕迹。
6. 不做复杂一致性审查：默认不要调用 detect_worldbuilding_conflicts，除非用户明确要求检查设定矛盾。
7. 不做角色扮演预演：默认不要调用 roleplay_character 或 dialogue_battle，除非用户明确要求"让角色先扮演/对话推演"。
8. 不做复杂剧情设计：默认不要调用 design_plot。章节写作直接基于大纲和用户要求调用 chapter_writer。
9. 回复简短：告诉用户完成了什么、创建/修改了哪些对象即可，不展示原始 JSON 或冗长推理。
10. 工具结果必须如实汇报：任何工具返回 error 或 skipped，都不能在最终回复中标记为成功。

【检索策略】
- 创建/修改角色：先用 list_characters 或 search_characters 检查目标是否存在或重名。
- 创建/修改世界观：先用 list_worldbuilding 或 search_worldbuilding 检查目标是否存在或重名。
- 创建/修改大纲：优先用 search_outline；需要连续规划时可用 search_outline_tree 看相邻结构。
- 创建/重写章节：只围绕目标大纲查信息。优先 search_outline 获取目标大纲节点；必要时 list_chapters 确认章节是否已存在。不要默认读取完整前文。
- 如果历史搜索记录中已有可用 ID 或目标信息，直接使用，不重复搜索。
- 一次搜索未找到就停止换花样重试；需求明确时直接创建新对象。

【角色快速流程】
创建角色：
1. list_characters 或 search_characters 检查是否重名。
2. 不重名则 character_writer 生成角色卡。
3. create_character 保存。
4. 简短回复。

修改角色：
1. search_characters 找目标。
2. update_character 直接修改。
3. 简短回复。

删除角色：
1. search_characters 找唯一目标。
2. delete_character 删除。
3. 简短回复。目标不唯一时才询问用户。

不要额外检查完整关系网、角色弧线、质量评分。

【世界观快速流程】
创建世界观：
1. list_worldbuilding 或 search_worldbuilding 检查是否同名。
2. 不同名则 worldbuilding_writer 生成条目。
3. create_worldbuilding_entry 保存。
4. 简短回复。

修改/删除世界观：
1. search_worldbuilding 找目标。
2. update_worldbuilding_entry 或 delete_worldbuilding_entry。
3. 简短回复。

默认不调用 detect_worldbuilding_conflicts。

【大纲快速流程】
创建大纲：
1. search_outline 或 search_outline_tree 获取相邻节点。
2. outline_writer 生成简洁大纲节点。
3. create_outline_node 保存。
4. 如果用户要求连续规划，按用户设置的连续规划章数生成，当前设置为 {outline_batch_count}。
5. 简短回复。

修改/删除大纲：
1. search_outline 找目标。
2. update_outline_node 或 delete_outline_node。
3. 简短回复。

不要做三幕式完整性评估、节奏评分、全书结构审查。

【章节快速流程】
创建新章节：
1. search_outline 找目标大纲节点。若用户给了明确剧情但没有对应大纲，先用 outline_writer 快速生成一个简短大纲节点并 create_outline_node 保存，不要因为缺大纲反复询问。
2. 如需避免重复章节标题，可用 list_chapters 快速确认。
3. 直接调用 chapter_writer 生成正文。输入重点放在目标大纲、用户要求、必要角色名，不要默认读取完整章节正文。
4. 直接 create_chapter 保存正文。优先传 chapter_writer 返回的 draft_id/content_ref，不要复制整章 content，防止长正文在工具参数中截断。
5. 保存后用 detect_character_changes 检测角色状态变化，优先传 create_chapter 返回的 chapter_id；如果还没保存则传 draft_id/content_ref。
6. 用 detect_new_worldbuilding 检测新增设定，优先传 chapter_id 或 draft_id/content_ref，不要复制整章正文；如有重要条目，用 worldbuilding_writer 生成简短条目，再 create_worldbuilding_entry 保存。
7. 简短回复章节标题、关联大纲、顺手更新的角色或世界观。

不要执行：
- evaluate_chapter
- detect_forbidden_patterns
- detect_worldbuilding_conflicts
- design_plot
- roleplay_character / dialogue_battle
- 多轮质量重写
- 全量角色读取
- 全量世界观读取
- 全量章节正文读取

重写已有章节：
1. search_chapters 找目标章节正文。
2. search_outline 找对应大纲节点。
3. 直接 chapter_writer 按用户要求重写。
4. update_chapter 保存。优先传 chapter_writer 返回的 draft_id/content_ref，不要复制整章 content。
5. detect_character_changes + detect_new_worldbuilding 更新必要资料，优先传 chapter_id 或 draft_id/content_ref。
6. 简短回复。

局部文本修改：
- 用户选中文本并要求润色、扩写、续写时，直接 rewrite_text、expand_text 或 continue_text。
- 不读取无关资料，不走整章重写流程。

【从 0 创建小说】
如果项目为空且用户要求从 0 开始：
1. worldbuilding_writer -> create_worldbuilding_entry，创建 1-3 条核心世界观。
2. character_writer -> create_character，创建 2-5 个核心角色。
3. outline_writer -> create_outline_node，创建 3-5 个开篇大纲节点。
4. 简短告诉用户基础已搭好，可以开始写第一章。

【什么时候询问用户】
只在以下情况暂停询问：
1. 用户意图太模糊，无法判断要创建什么。
2. 删除目标不唯一，可能误删。
3. 重写章节会覆盖大量正文，但用户没有明确说要覆盖。
4. 项目为空，且用户没有给任何题材、主角、方向。

除此之外，快速模式应尽量直接完成。

【自动执行设置】
当前自动执行工具：{auto_apply_text}。如果系统允许自动执行，就直接写入；如果系统不允许，仍要给出明确的拟执行动作，避免长篇解释。
""".strip()


def build_workspace_assistant_system_prompt(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
    mode: str = "quality",
) -> str:
    """Backward-compatible wrapper — delegates to the pack system."""
    from .packs.workspace_fast import PACK as WORKSPACE_FAST_PACK
    from .packs.workspace_quality import PACK as WORKSPACE_QUALITY_PACK
    from ..services.agent.prompt_builder import build_system_prompt
    pack = WORKSPACE_FAST_PACK if mode == "fast" else WORKSPACE_QUALITY_PACK
    return build_system_prompt(pack, scope=scope, outline_batch_count=outline_batch_count, auto_apply=auto_apply)


def format_memory_context(memories: list[dict]) -> str:
    """Format a list of memory dicts into a prompt-ready context block."""
    if not memories:
        return ""
    lines = ["【持久记忆 — 已保存的用户偏好和知识，优先参考】"]
    for m in memories:
        cat = m.get("category", "user_preference")
        cat_label = {
            "user_preference": "用户偏好", "project_fact": "项目事实",
            "writing_style": "写作风格", "research_note": "研究笔记",
            "workflow_preference": "工作流偏好",
            "preference": "用户偏好", "fact": "项目事实",
            "search_result": "研究笔记", "note": "笔记",
        }.get(cat, cat)
        lines.append(f"- [{cat_label}] {m.get('key','')}：{m.get('value','')}")
    return "\n".join(lines) + "\n"


def build_workspace_assistant_initial_user_message(
    *,
    project_title: str,
    project_description: str | None,
    style_context: str,
    history_text: str,
    selected_context: list[str],
    previous_search_context: str = "",
    memory_context: str = "",
    outline_batch_count: int,
    auto_apply: bool,
    user_message: str,
) -> str:
    selected_text = "\n".join(selected_context) or "当前没有选中对象。"
    search_context_block = f"\n\n{previous_search_context}" if previous_search_context.strip() else ""
    memory_block = f"\n\n{memory_context}" if memory_context.strip() else ""
    return (
        f"作品：{project_title}\n"
        f"简介：{project_description or '暂无'}\n"
        f"写作风格与禁用表达：\n{style_context}\n\n"
        f"【历史对话 — 仅供参考，不要重复执行历史中的操作】\n{history_text}\n\n"
        f"{selected_text}{search_context_block}{memory_block}\n\n"
        f"用户设置：连续规划章数={outline_batch_count}；自动执行工具={auto_apply}。\n\n"
        f"【当前任务 — 必须执行】\n{user_message}\n\n"
        "重要提醒：你的任务是执行【当前任务】中的最新指令，而不是重复历史对话中的旧操作。"
        "历史对话仅用于理解上下文，不要照搬其中的工具调用。\n\n"
        "提示：以上「历史搜索记录」是你之前所有轮次搜到的真实数据（已去重），可以直接信任使用，无需重复搜索。"
        "如果目标 ID 或信息已在其中，直接引用即可。"
        "只有在你需要的信息不在历史记录中时，才用 search_* 工具补充查询。"
    )


def _compress_search_result(result: dict) -> dict | None:
    """Compress a single search result for persistent context — lightweight fields only."""
    tool = str(result.get("tool") or "")
    data = result.get("data")
    if not isinstance(data, list) or not data:
        return None
    compressed = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry: dict = {}
        for key in ("id", "name", "title", "dimension", "role_type", "outline_node_id", "node_type", "direction", "target_name", "relationship_type"):
            if key in item:
                entry[key] = item[key]
        # Truncate long text fields
        for key in ("content", "summary", "personality", "background", "description"):
            if key in item and item[key]:
                text = str(item[key])
                entry[key] = text[:300] + ("..." if len(text) > 300 else "")
        if "children" in item:
            entry["children_count"] = len(item["children"])
        if not entry:
            continue
        compressed.append(entry)
    if not compressed:
        return None
    return {"tool": tool, "detail": result.get("detail", ""), "data": compressed}


def format_previous_search_context(search_results: list[dict], max_chars: int = 200_000) -> str:
    """Format compressed search results from previous turns as injectable context.

    Tools are included in order until max_chars is reached. When a tool's data would
    exceed the limit, it is skipped entirely (never truncated mid-JSON) and remaining
    tools are counted in a summary line.
    """
    if not search_results:
        return ""
    # Prioritize lightweight list tools first, then heavier search tools, newest first within each
    list_results = [r for r in search_results if str(r.get("tool", "")).startswith("list_")]
    search_results_ordered = list_results + [r for r in search_results if r not in list_results]

    lines = ["【历史搜索记录（以下是你之前搜到的真实数据，可直接使用对应 ID，无需重复搜索）】"]
    total = 0
    included = 0
    omitted_tools = 0
    omitted_entries = 0
    for r in search_results_ordered:
        tool = r.get("tool", "?")
        detail = r.get("detail", "")
        data = r.get("data", [])
        if not data:
            continue
        snippet = _json.dumps(data, ensure_ascii=False)
        if total + len(snippet) > max_chars:
            omitted_tools += 1
            omitted_entries += len(data)
            continue
        total += len(snippet)
        included += 1
        lines.append(f"\n{tool}: {detail}")
        lines.append(snippet)
    if omitted_tools:
        lines.append(f"\n（注：还有 {omitted_tools} 个搜索工具、共 {omitted_entries} 条结果因上下文限制省略，如需可重新搜索）")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_tool_result_message(
    iteration: int,
    tool_results: list[dict],
) -> str:
    """Format search tool results as a user message fed into the next LLM call."""
    lines = [f"【第 {iteration} 轮搜索结果】"]
    for r in tool_results:
        r = redact_tool_result_for_model(r)
        tool = r.get("tool", "?")
        status = r.get("status", "?")
        detail = r.get("detail", "")
        lines.append(f"\n{tool}: {status} — {detail}")
        data = r.get("data")
        if data is not None:
            lines.append(_json.dumps(data, ensure_ascii=False))
    lines.append(f"\n---\n请基于以上第 {iteration} 轮搜索结果继续推理。如果信息已足够，直接给出文本回复或调用写入工具。")
    return "\n".join(lines)


def redact_tool_result_for_model(result: dict) -> dict:
    """Keep tool feedback compact while preserving references the model needs."""
    if not isinstance(result, dict) or result.get("tool") != "chapter_writer":
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    content = str(data.get("content") or "")
    if not content:
        return result
    compact_data = dict(data)
    compact_data["content_preview"] = content[:500] + ("..." if len(content) > 500 else "")
    compact_data.pop("content", None)
    compact_data["usage_note"] = "后续 create_chapter/update_chapter/evaluate_chapter/detect_* 工具请传 draft_id 或 content_ref，不要复制整章 content。"
    return {**result, "data": compact_data}
