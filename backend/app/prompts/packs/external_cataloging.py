"""External Cataloging pack — API-free cataloging for external agents (Claude Code / Codex)."""
from __future__ import annotations

from . import PromptPack


def _build_external_cataloging_system() -> str:
    from ...services.prompt_packs.seed import BUILTIN_PACKS
    pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
    return pack["system_prompt"]


EXTERNAL_CATALOGING_PACK = PromptPack(
    name="cataloging_external_no_api",
    version="1.0",
    pack_type="cataloging",
    description="API-free cataloging for external agents (Claude Code / Codex). Extracts characters, worldbuilding, outline, and chapter summaries without Moshu model API.",
    input_fields=["project_id", "chapter_ids"],
    max_token_budget=8000,
    output_format="jsonl",
    output_schema=None,
    available_tools=[
        "start_external_cataloging_job",
        "get_next_external_cataloging_chapter",
        "save_external_cataloging_facts",
        "save_external_cataloging_candidates",
        "verify_external_cataloging_progress",
        "apply_pending_cataloging",
        "get_project_archive_status",
        "search_characters",
        "search_worldbuilding",
        "search_outline",
    ],
    unavailable_tools=[
        "chapter_writer", "character_writer", "outline_writer",
        "worldbuilding_writer", "design_plot", "evaluate_chapter",
        "start_cataloging_job",
    ],
    forbidden_behaviors=[
        "不要调用需要墨枢 API 的工具",
        "不要报告完成除非验证通过",
        "不要跳过读写验证",
        "不要创建重复的角色或世界观条目",
        "不要忽略工具返回的错误",
    ],
    default_temperature=0.1,
    default_max_tokens=8000,
    tool_policy="restricted",
    build_system_prompt=_build_external_cataloging_system,
)
