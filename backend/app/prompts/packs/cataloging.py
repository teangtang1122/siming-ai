"""Cataloging packs — wraps existing cataloging prompts as discoverable packs."""
from __future__ import annotations

from . import PromptPack


def _build_fact_extraction_system() -> str:
    from ...services.cataloging.staged_prompts import FACT_EXTRACTION_SYSTEM_PROMPT
    return FACT_EXTRACTION_SYSTEM_PROMPT


def _build_resolution_system() -> str:
    from ...services.cataloging.staged_prompts import CATALOGING_RESOLUTION_SYSTEM_PROMPT
    return CATALOGING_RESOLUTION_SYSTEM_PROMPT


def _build_legacy_system() -> str:
    from ...services.cataloging.prompts import CATALOGING_SYSTEM_PROMPT
    return CATALOGING_SYSTEM_PROMPT


FACT_EXTRACTION_PACK = PromptPack(
    name="cataloging_fact_extraction",
    version="1.0",
    pack_type="cataloging",
    description="Stage 1: bare-read chapter fact extraction for project cataloging",
    input_fields=["chapter_content", "chapter_title"],
    max_token_budget=8000,
    output_format="jsonl",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止输出 Markdown 或代码块",
        "禁止输出 character_create/worldbuilding_create 等写库类型",
        "禁止把原文大段复制进 evidence",
    ],
    default_temperature=0.2,
    default_max_tokens=8000,
    context_budget={"chapter_content": 6000},
    tool_policy="none",
    build_system_prompt=_build_fact_extraction_system,
)

RESOLUTION_PACK = PromptPack(
    name="cataloging_resolution",
    version="1.0",
    pack_type="cataloging",
    description="Stage 2: merge new facts with existing cards to produce write candidates",
    input_fields=["facts_text", "targeted_context", "chapter_title"],
    max_token_budget=12000,
    output_format="jsonl",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止输出 Markdown 或代码块",
        "禁止在 update payload 中输出未变化的字段",
    ],
    default_temperature=0.2,
    default_max_tokens=12000,
    context_budget={"facts_text": 4000, "targeted_context": 6000},
    tool_policy="none",
    build_system_prompt=_build_resolution_system,
)

LEGACY_PACK = PromptPack(
    name="cataloging_legacy",
    version="1.0",
    pack_type="cataloging",
    description="Single-pass cataloging (legacy path)",
    input_fields=["context", "chapter_title", "chapter_content"],
    max_token_budget=12000,
    output_format="jsonl",
    output_schema=None,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止输出 Markdown 或代码块",
    ],
    default_temperature=0.2,
    default_max_tokens=12000,
    context_budget={"chapter_content": 8000},
    tool_policy="none",
    build_system_prompt=_build_legacy_system,
)
