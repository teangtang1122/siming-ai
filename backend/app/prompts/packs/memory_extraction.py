"""Memory extraction pack — extract user preferences from conversation."""
from __future__ import annotations

from . import PromptPack

OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["user_preference", "project_fact", "writing_style", "research_note", "workflow_preference"],
            },
            "key": {"type": "string"},
            "value": {"type": "string"},
            "evidence": {"type": "string", "description": "用户原话中触发该偏好的语句（必须是用户原文片段，不能来自助手回复）"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["category", "key", "value", "evidence", "importance"],
    },
}


def _build_system() -> str:
    return (
        "你是用户偏好记忆提取器。从对话中识别需要持久化的用户偏好和知识。\n\n"
        "【最重要规则】\n"
        "- evidence 字段必须是用户原话的直接引用片段，不能来自助手回复\n"
        "- 只提取用户明确表达或强烈暗示的偏好，不要从助手建议中推断\n"
        "- importance >= 7 才输出，否则跳过\n\n"
        "【提取分类】\n"
        "1. user_preference（用户偏好）：用户的写作习惯、角色偏好、对话偏好、世界观偏好等\n"
        "   例：'我不喜欢太文艺的文风' → category='user_preference', key='文风偏好', value='不喜欢太文艺的文风'\n"
        "   例：'主角名字不要太长' → category='user_preference', key='命名习惯', value='主角名字不要太长'\n"
        "2. writing_style（写作风格）：用户对叙事视角、文体风格、节奏等的具体要求\n"
        "   例：'用第三人称有限视角' → category='writing_style'\n"
        "3. workflow_preference（工作流偏好）：用户对助手工作方式的偏好\n"
        "   例：'每次写完先给我看看再保存' → category='workflow_preference'\n"
        "4. project_fact（项目事实）：用户明确陈述的项目设定或世界观事实\n"
        "   例：'这个项目的世界观是修仙' → category='project_fact'\n"
        "5. research_note（研究笔记）：联网搜索或用户提供的参考资料\n"
        "   只在用户明确提供资料或搜索结果时提取\n\n"
        "【输出格式】\n"
        "输出 JSON 数组，每条包含：\n"
        "- category: 分类（user_preference/writing_style/workflow_preference/project_fact/research_note）\n"
        "- key: 偏好标识（简短概括）\n"
        "- value: 偏好内容（具体描述）\n"
        "- evidence: 用户原话中触发该偏好的语句（必须是用户原文片段）\n"
        "- importance: 重要性 1-10（只提取 >= 7 的条目）\n\n"
        "没有可提取的偏好时输出空数组 []。\n"
        "只输出 JSON 数组，不要加任何解释或 Markdown 格式。"
    )


PACK = PromptPack(
    name="memory_extraction",
    version="2.0",
    pack_type="memory",
    description="Extract user preferences and knowledge from conversation for persistent memory",
    input_fields=["conversation_text"],
    max_token_budget=2000,
    output_format="json",
    output_schema=OUTPUT_SCHEMA,
    available_tools=[],
    unavailable_tools=[],
    forbidden_behaviors=[
        "禁止从助手回复中推断用户偏好",
        "禁止提取 importance < 7 的条目",
        "禁止输出 JSON 数组以外的内容",
        "每条必须包含 evidence 字段且必须是用户原话",
    ],
    default_temperature=0.2,
    default_max_tokens=2000,
    context_budget={"conversation_text": 2000},
    tool_policy="none",
    build_system_prompt=_build_system,
)
