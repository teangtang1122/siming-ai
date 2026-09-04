"""Workspace quality pack backed by the compiled PromptSpec catalog."""
from __future__ import annotations

from ...modules.assistant.infrastructure.runtime import render_prompt
from . import PromptPack


def _build_system(
    *,
    outline_batch_count: int,
    **_: object,
) -> str:
    """Render the entrypoint-neutral project Agent prompt."""
    return render_prompt(
        "assistant.workspace.quality",
        outline_batch_count=outline_batch_count,
    )


PACK = PromptPack(
    name="workspace_quality",
    version="3.2.0",
    pack_type="workspace",
    description="Compiled workspace controller with truthful tool outcomes",
    input_fields=["outline_batch_count"],
    max_token_budget=4000,
    output_format="text_reply",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止在信息不充分时输出最终回复",
        "禁止用文件写入冒充数据库写入",
        "禁止把失败、跳过或空结果说成已完成",
        "禁止重复执行历史对话中的操作",
    ],
    default_temperature=0.3,
    default_max_tokens=4000,
    tool_policy="dynamic_selected",
    build_system_prompt=_build_system,
)
