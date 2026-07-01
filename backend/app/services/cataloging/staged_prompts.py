"""Prompt templates for staged project cataloging."""
from __future__ import annotations

from app.prompts.cataloging_source import (
    get_cataloging_candidate_rules,
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

CATALOGING_MERGED_SYSTEM_PROMPT = "\n\n".join([
    "你是“作品建档”的单阶段实验决策器。",
    "你会收到当前章节正文，以及已有角色、世界观、大纲和近期章节摘要。你的任务不是先保存事实卡片，而是直接输出可写入数据库的候选 JSONL。",
    "信息关注范围必须等同第一阶段事实抽取：只采集会影响大纲、角色、关系、世界观或后续写作连续性的内容；不要复述普通动作流水账。",
    "只输出 JSONL；每一行是一个完整 JSON 对象；不要输出 Markdown、解释、代码块或 JSON 数组。",
    "必须至少输出 1 条 chapter_summary 和 1 条 node_type=\"chapter\" 的 outline_create；有多个重要场景时额外输出 section 级 outline_create。",
    "不要输出 chapter_overview、character_fact、worldbuilding_fact、outline_fact 或 relationship_fact；这些是两阶段流程的中间事实类型，本实验流程禁止使用。",
    get_language_rules(),
    get_cataloging_candidate_rules(),
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
def build_merged_cataloging_prompt(
    *,
    chapter_title: str,
    chapter_content: str,
    context_json: str,
    chapter_file: str = "",
    project_folder: str = "",
    use_file_references: bool = False,
) -> str:
    if use_file_references:
        return f"""当前章节标题：{chapter_title}

当前章节 UTF-8 文件：{chapter_file}
项目镜像目录：{project_folder}

请直接读取章节文件，并读取项目镜像中的 characters/、worldbuilding/、outline/、summaries/ 等已有档案文件。不要要求系统把正文或档案重新粘贴进提示词。

请按系统规则直接输出候选 JSONL：chapter_summary、outline_create、character_create/update/state_update、character_relationship、worldbuilding_create/update/timeline、chapter_link 等。
禁止输出 fact JSONL。"""
    return f"""当前章节标题：{chapter_title}

当前章节正文：
{chapter_content}

已有角色、世界观、大纲和近期摘要上下文：
{context_json}

请按系统规则直接输出候选 JSONL。禁止输出 fact JSONL。"""
