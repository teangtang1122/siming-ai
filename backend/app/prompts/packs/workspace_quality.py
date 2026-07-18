"""Workspace quality pack backed by the compiled PromptSpec catalog."""
from __future__ import annotations

from ...modules.assistant.infrastructure.runtime import render_prompt
from ..workspace_contract import SCOPE_LABELS, WORKSPACE_TOOL_NAMES
from . import PromptPack

ALL_WORKSPACE_TOOL_NAMES = WORKSPACE_TOOL_NAMES


def _build_system(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
    tool_names: list[str] | set[str] | None = None,
) -> str:
    """Render the unified controller prompt for the tools in this turn."""
    available = set(tool_names) if tool_names is not None else ALL_WORKSPACE_TOOL_NAMES
    return render_prompt(
        "assistant.workspace.quality",
        scope_label=SCOPE_LABELS.get(scope, "项目规划"),
        outline_batch_count=outline_batch_count,
        auto_apply="是" if auto_apply else "否",
        tool_names=", ".join(sorted(available)),
    )


PACK = PromptPack(
    name="workspace_quality",
    version="3.0.0",
    pack_type="workspace",
    description="Compiled workspace controller with truthful tool outcomes",
    input_fields=["scope", "outline_batch_count", "auto_apply"],
    max_token_budget=4000,
    output_format="text_reply",
    output_schema=None,
    available_tools=sorted(ALL_WORKSPACE_TOOL_NAMES),
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止在信息不充分时输出最终回复",
        "禁止跳过 evaluate_chapter 直接 create_chapter",
        "禁止用文件写入冒充数据库写入",
        "禁止把失败、跳过或空结果说成已完成",
        "禁止重复执行历史对话中的操作",
    ],
    default_temperature=0.3,
    default_max_tokens=4000,
    tool_policy="full",
    build_system_prompt=_build_system,
)
