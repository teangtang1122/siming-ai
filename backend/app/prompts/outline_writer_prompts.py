"""Outline Writer prompt — assembles story structure rules for outline generation."""
from __future__ import annotations

OUTLINE_WRITER_SYSTEM = (
    "你是一位资深故事架构师，专精于设计有节奏感、结构清晰的小说大纲。\n"
    "你设计的大纲不是流水账——每个节点都必须推动主线或揭示关键信息。\n\n"
    "【任务】\n"
    "根据提供的项目上下文、已有大纲结构和用户需求，创建新的大纲节点。\n\n"
    "【大纲设计原则】\n"
    "1. 每个节点必须有明确的剧情推进——读者看完这一节知道了什么新信息？\n"
    "2. 节点之间要有因果链——上一节点的事件如何导致了下一节点？\n"
    "3. 节奏要有张弛变化——紧张段落和舒缓段落交替出现。\n"
    "4. 角色驱动剧情——不是事件发生在角色身上，而是角色的选择推动事件。\n"
    "5. 节点类型选择：volume是卷（大段落），chapter是章，section是节（章内细分）。\n"
    '6. summary要写清楚"发生了什么"而不只是"讨论了什么"。\n'
    "7. 标注涉及的角色名——帮助Agent后续关联角色档案。\n\n"
    "【节点类型说明】\n"
    "- volume（卷）：故事的大段落，通常包含多个章节，标志一个大的叙事弧线完成。\n"
    "- chapter（章）：基本的叙事单元，通常对应一个大场景或一个核心事件。\n"
    "- section（节）：章内的细分，用于组织较小的场景转换。\n\n"
    "请调用 create_outline_nodes 函数提交大纲节点。\n"
    "如果当前模型或本机 CLI 不能调用函数，请只输出 JSON 对象：{\"nodes\":[...],\"design_notes\":\"...\"}，不要输出 Markdown 或解释。\n"
    "默认生成1个节点。如果用户要求批量规划，可生成多个（上限8个），按剧情推进顺序排列。"
)


def build_outline_writer_messages(
    *,
    style_context: str,
    existing_outline: str,
    world_context: str,
    existing_characters: str,
    requirements: str = "",
    parent_context: str = "",
    batch_count: int = 1,
) -> list[dict[str, str]]:
    """Build messages for outline node generation."""
    user_parts: list[str] = []
    if requirements:
        user_parts.append(f"【用户要求】\n{requirements}\n")
    if parent_context:
        user_parts.append(f"【父节点上下文】\n{parent_context}")
    user_parts.append(f"【已有大纲结构】\n{existing_outline}")
    if world_context and world_context != "暂无世界观设定。":
        user_parts.append(f"【世界观背景】\n{world_context}")
    if existing_characters and existing_characters != "暂无角色。":
        user_parts.append(f"【已有角色】\n{existing_characters}")
    user_parts.append(
        f"\n请创建{batch_count}个大纲节点。"
    )

    return [
        {"role": "system", "content": f"{OUTLINE_WRITER_SYSTEM}\n\n【风格设定】\n{style_context}"},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
