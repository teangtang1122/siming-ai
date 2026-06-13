"""Prompt templates for staged project cataloging."""
from __future__ import annotations

from app.prompts.cataloging_source import (
    get_candidate_resolution_rules,
    get_cataloging_candidate_schema,
    get_fact_extraction_rules,
    get_language_rules,
)
from app.prompts.prompt_source import get_naming_resolution_rules, get_time_tracking_rules

FACT_EXTRACTION_SYSTEM_PROMPT = "\n\n".join([
    "你是“作品建档”的第一阶段事实抽取器。",
    get_fact_extraction_rules(),
])


CATALOGING_RESOLUTION_SYSTEM_PROMPT = "\n\n".join([
    "你是“作品建档”的第二阶段决策器。",
    get_language_rules(),
    get_candidate_resolution_rules(),
    get_time_tracking_rules(),
    get_naming_resolution_rules(),
    get_cataloging_candidate_schema(),
])


def build_fact_extraction_prompt(chapter_title: str, chapter_content: str) -> str:
    return f"""当前章节标题：
{chapter_title}

当前章节正文：
{chapter_content}

请按系统规则输出事实 JSONL。先输出 chapter_overview，再输出角色、关系、世界观、大纲和身份线索事实。"""


def build_resolution_prompt(facts_jsonl: str, context_json: str, chapter_title: str) -> str:
    return f"""当前章节标题：
{chapter_title}

第一阶段事实 JSONL：
{facts_jsonl}

相关旧资料与索引：
{context_json}

请基于“事实 + 相关旧资料”输出最终候选 JSONL。旧资料是紧凑上下文，只有命中内容才展开；不要因为索引里出现名称就强行更新。"""
