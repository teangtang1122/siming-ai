"""Prompt templates for the shared workspace assistant — agentic multi-turn variant."""
from __future__ import annotations

import json as _json

AVAILABLE_WORKSPACE_TOOLS = (
    "list_characters, list_chapters, list_worldbuilding, search_characters, search_chapters, search_outline, search_outline_tree, search_worldbuilding, search_relationships, "
    "create_worldbuilding_entry, update_worldbuilding_entry, delete_worldbuilding_entry, "
    "create_character, update_character, delete_character, "
    "create_relationship, update_relationship, delete_relationship, "
    "create_outline_node, create_outline_nodes, update_outline_node, delete_outline_node, "
    "create_chapter, update_chapter, delete_chapter, list_chapter_versions, restore_chapter_version, diff_chapter_versions, "
    "chapter_writer, character_writer, outline_writer, worldbuilding_writer, "
    "roleplay_character, dialogue_battle, "
    "rewrite_text, expand_text, continue_text, "
    "suggest_conflicts, design_plot, detect_character_changes, detect_new_worldbuilding, archive_chapter_after_write, inspect_story_granularity, repair_story_granularity, detect_worldbuilding_conflicts, detect_forbidden_patterns, evaluate_chapter, "
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


# NOTE: build_fast_workspace_assistant_system_prompt() was removed. The entry
# point delegates to prompt_builder.get_workspace_pack(), which intentionally
# normalizes all modes to the quality pack so every entrypoint follows the same
# behavior standard.


def build_workspace_assistant_system_prompt(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
    mode: str = "quality",
) -> str:
    """Backward-compatible wrapper — delegates to the pack system."""
    from ..services.agent.prompt_builder import build_system_prompt, get_workspace_pack
    pack = get_workspace_pack(mode)
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
