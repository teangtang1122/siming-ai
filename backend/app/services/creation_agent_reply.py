"""Plain-reply contract, also exported to the standalone Android agent."""
from __future__ import annotations

import re
from typing import Any

from app.modules.creation.interfaces.agent_scope import CREATION_AGENT_WRITE_TOOL_NAMES

CREATION_REPLY_MAX_ATTEMPTS = 2
CREATION_READ_ONLY_COMPLETION_INSTRUCTION = (
    "本轮目前只有读取，尚未生成或保存阶段资料。请按作者最新消息决定下一步："
    "要求生成或修改资料时，继续用写工具保存对应 artifact，最终审阅也须写入 final_review；"
    "拿到成功回执后才算完成。只询问或查看时可直接回答，并准确说明已有状态。"
    "聊天中的报告和类别切换都不是保存回执。"
)
CREATION_READ_ONLY_NOTICE = "本轮只读取了立项资料，尚未生成或保存新的阶段资料。"
CREATION_REPLY_INSTRUCTION = (
    "请根据以上真实工具返回，用两到四句中文说明本轮实际完成的读取、修改或任务启动，"
    "并提出至多一个基于当前立项数据的后续问题。不得声称未成功的写入已经保存；"
    "running 只表示任务已启动，不表示内容已生成。"
    "data.saved=true 表示生成结果已经保存；requires_confirmation=true 表示等待作者审阅确认，"
    "不是等待生成。collection_counts 是保存后的实际条目数，已有结果应按回执说明，不再猜测数量。"
    "当前状态以最新写入回执的 revision 为准，不能把写入前快照中的空值当作当前状态。"
    "没有成功写入时，只能说明查看了资料，不能说已生成、已保存或已完成阶段。"
    "本轮工具已关闭，当前唯一任务是向作者说明已有执行回执，不再规划或执行下一步。"
    "写入成功回执已经确认提交，不需要再次读取验证。"
    "只返回面向作者的自然语言，不得返回 tool_calls，也不得用 DSML、XML 或 JSON 模拟工具调用。"
)
CREATION_REPLY_REPAIR_INSTRUCTION = (
    "上一条总结未通过输出协议校验，未向作者显示，也未执行其中的任何工具。"
    "已完成的真实操作保持不变。请只纠正总结，工具继续关闭，不得重放操作。"
)
CREATION_REPLY_FAILURE_NOTICE = "模型未能生成有效总结；本轮已结束，不会自动重复执行操作。"
# Protocol namespace validation only; never parse these strings into executable calls.
CREATION_REPLY_TOOL_MARKUP_PATTERN = (
    r"(?i)(?:<|&lt;)\s*(?:\\?/?[｜|]+DSML(?:[｜|]+|(?=\s*$))"
    r"|/?tool_call\b|/?function\s*=|/?parameter\s*=)"
)
_TOOL_MARKUP = re.compile(CREATION_REPLY_TOOL_MARKUP_PATTERN, re.IGNORECASE)


def creation_reply_error(content: Any, tool_calls: Any = None) -> str | None:
    if tool_calls:
        return "unexpected_tool_calls"
    if not isinstance(content, str) or not content.strip():
        return "empty_reply"
    if _TOOL_MARKUP.search(content):
        return "tool_protocol_text"
    return None


def creation_receipt_reply(
    tool_results: list[dict[str, Any]],
    write_results: list[dict[str, Any]],
    *,
    tool_mode: str,
) -> str:
    """Report deterministic execution facts when a model reply is unavailable."""
    if write_results:
        running = any(item.get("status") == "running" for item in write_results)
        reply = (
            "本轮任务已启动，请在任务状态中查看结果。"
            if running else "本轮修改已保存，请在立项资料中查看结果。"
        )
        details = [
            str(item.get("detail")) for item in write_results[:3]
            if creation_reply_error(item.get("detail")) is None
        ]
        return reply + (f"回执：{'；'.join(details)}。" if details else "")
    failures = [
        str(item.get("detail") or "工具未完成")
        for item in tool_results
        if item.get("status") not in {"ok", "running"}
    ]
    if failures:
        detail = failures[-1] if creation_reply_error(failures[-1]) is None else "工具未完成"
        return f"本轮没有保存任何修改：{detail}。请调整要求后重试。"
    if any(
        item.get("status") == "ok" and item.get("tool") not in CREATION_AGENT_WRITE_TOOL_NAMES
        for item in tool_results
    ):
        return "本轮只完成了立项工具读取，没有保存任何修改。请明确要写入的对象和内容后重试。"
    if tool_mode == "direct_mcp":
        return "本轮没有获得可验证的 MCP 结果，因此无法确认读取或修改了立项数据。请重试。"
    if tool_results:
        return "本轮执行了立项工具，但没有产生可确认的写入。请调整要求后重试。"
    return "本轮未执行任何立项工具，因此没有读取或修改立项数据。请重试。"
