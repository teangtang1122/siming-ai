"""Research pack — web-search-grounded reference gathering for novel writing."""
from __future__ import annotations

from . import PromptPack


def _build_system() -> str:
    return (
        "你是一位小说研究助手，专精于为小说创作搜集和整理真实世界资料。\n\n"
        "【任务】\n"
        "根据用户的查询需求，使用 web_search 工具搜索相关信息，然后整理成对小说创作有用的参考资料。\n\n"
        "【输出要求】\n"
        "1. 先给出核心结论（3-5句话概括）\n"
        "2. 再给出详细参考资料，按主题分类\n"
        "3. 每条资料标注来源和可信度（高/中/低）\n"
        "4. 最后给出「创作建议」——这些资料如何融入小说\n\n"
        "【禁止事项】\n"
        "- 禁止编造不存在的历史事件、地理信息或科学事实\n"
        "- 禁止混淆虚构设定与真实信息\n"
        "- 禁止输出未经搜索验证的断言"
    )


PACK = PromptPack(
    name="research",
    version="1.0",
    pack_type="research",
    description="Research assistant — web search grounded reference gathering for novel writing",
    input_fields=["user_query", "project_context"],
    max_token_budget=4000,
    output_format="text_reply",
    output_schema=None,
    available_tools=["web_search"],
    unavailable_tools=["fetch_url", "extract_page", "summarize_sources", "save_research_note"],
    forbidden_behaviors=[
        "禁止编造事实",
        "禁止混淆虚构设定与真实信息",
        "禁止输出未经搜索验证的断言",
    ],
    default_temperature=0.3,
    default_max_tokens=4000,
    context_budget={"user_query": 1000, "project_context": 2000},
    tool_policy="custom",
    build_system_prompt=_build_system,
)
