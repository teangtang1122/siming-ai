"""Workspace fast compatibility pack backed by PromptSpec."""
from __future__ import annotations

from ...modules.assistant.infrastructure.runtime import render_prompt
from ..workspace_contract import SCOPE_LABELS
from . import PromptPack
from .workspace_quality import ALL_WORKSPACE_TOOL_NAMES


def _build_system(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
    tool_names: list[str] | set[str] | None = None,
) -> str:
    """Render the shared controller through the fast compatibility spec."""
    available = set(tool_names) if tool_names is not None else ALL_WORKSPACE_TOOL_NAMES
    return render_prompt(
        "assistant.workspace.fast",
        scope_label=SCOPE_LABELS.get(scope, "项目规划"),
        outline_batch_count=outline_batch_count,
        auto_apply="是" if auto_apply else "否",
        tool_names=", ".join(sorted(available)),
    )


PACK = PromptPack(
    name="workspace_fast",
    version="3.0.0-rc.1",
    pack_type="workspace",
    description="Compatibility fast workspace assistant — delegates to quality controller rules",
    input_fields=["scope", "outline_batch_count", "auto_apply"],
    max_token_budget=4000,
    output_format="text_reply",
    output_schema=None,
    available_tools=sorted(ALL_WORKSPACE_TOOL_NAMES),
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止降低质量版控制器规则",
        "禁止跳过质量模式要求的上下文预检与章节评估",
        "禁止让不同入口产生不同工作流",
    ],
    default_temperature=0.3,
    default_max_tokens=4000,
    context_budget={"style": 1000, "outline": 1500, "world": 1000},
    tool_policy="full",
    build_system_prompt=_build_system,
)
