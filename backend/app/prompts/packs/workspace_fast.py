"""Workspace Fast compatibility pack.

Fast mode is kept as a UI/workflow hint, but the controller prompt itself
delegates to the quality pack so every entrypoint follows the same behavior.
"""
from __future__ import annotations

from . import PromptPack
from .workspace_quality import ALL_WORKSPACE_TOOL_NAMES


def _build_system(
    *,
    scope: str,
    outline_batch_count: int,
    auto_apply: bool,
) -> str:
    """Compatibility wrapper: fast workspace control uses quality rules too."""
    from .workspace_quality import PACK as WORKSPACE_QUALITY_PACK

    return WORKSPACE_QUALITY_PACK.build_system_prompt(
        scope=scope,
        outline_batch_count=outline_batch_count,
        auto_apply=auto_apply,
    )


PACK = PromptPack(
    name="workspace_fast",
    version="2.0",
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
