"""Worldbuilding Writer prompt — assembles world design rules for entry generation."""
from __future__ import annotations

from .anti_ai_prompts import build_anti_ai_system_prompt

DIMENSION_GUIDANCE = {
    "geography": (
        "【地理维度 — 具体要求】\n"
        '- 描述具体的地形、气候、资源分布。不只是"有山有水"，要写出对这些地理条件的利用和限制。\n'
        "- 地理影响文化和势力——为什么这个势力在这里而不是别处？\n"
        '- 写出地点的"记忆"——这里发生过什么改变地貌的事件？\n'
    ),
    "history": (
        "【历史维度 — 具体要求】\n"
        '- 写具体的历史事件，不要写"历史悠久""源远流长"等空洞概括。\n'
        "- 历史事件必须有前因后果——谁做了什么？为什么？结果如何？\n"
        "- 历史对当下有影响——过去的哪个决定导致了现在的哪个局面？\n"
    ),
    "factions": (
        "【势力维度 — 具体要求】\n"
        "- 每个势力必须有：核心目标、资源/优势、内部矛盾、与其他势力的关系。\n"
        "- 势力内部不是铁板一块——写出派系、分歧、潜在的背叛者。\n"
        '- 势力之间的冲突不是"好人vs坏人"，而是目标和资源的碰撞。\n'
    ),
    "power_system": (
        "【规则体系维度 — 具体要求】\n"
        "- 规则必须有限制——没有限制的规则体系等于是没有规则。\n"
        "- 强弱有阶梯——不同角色对规则的掌握程度有层次。\n"
        "- 规则服务于剧情——不是为设定而设定，而是为角色提供困境和选择。\n"
        '- 写出规则的"边界"和"漏洞"——最有趣的剧情往往出现在规则的边缘。\n'
    ),
    "races": (
        "【种族维度 — 具体要求】\n"
        '- 避免种族本质主义——不写"这个种族天生邪恶/善良"。\n'
        '- 写文化差异而非生物学差异——不同种族的冲突源于资源、信仰、历史，而非"他们天生就是那样"。\n'
        "- 每个种族有内部多样性——不是每个成员都一样。\n"
    ),
    "culture": (
        "【文化维度 — 具体要求】\n"
        "- 文化体现在具体习俗中——节日、仪式、禁忌、饮食习惯、问候方式。\n"
        '- 文化解释角色的行为——为什么这个角色认为某件事"理所当然"？\n'
        '- 文化是活的——正在被挑战、被改变、被遗忘的传统比"万古不变"的传统更有戏剧性。\n'
    ),
}

WORLDBUILDING_WRITER_SYSTEM_BASE = (
    "你是一位资深世界观设计师，专精于创造有深度、逻辑自洽、服务于剧情的虚构世界。\n"
    "你不会堆砌设定——你创造的每个设定都有其叙事功能。\n\n"
    "【任务】\n"
    "根据提供的项目上下文、维度和用户需求，创建一条世界观设定条目。\n\n"
    "【世界观设计原则】\n"
    "1. 设定服务于剧情——这个设定能为角色制造什么样的问题或选择？\n"
    '2. 少即是多——只创建剧情需要的设定。不要为了"完整"而填满所有细节。\n'
    "3. 具体胜过抽象——写一个具体的规则比写十个模糊的概念更有用。\n"
    "4. 每个设定都有代价或限制——完美的设定是无聊的。\n"
    "5. 设定之间有关联——新的设定如何与已有设定互动？\n\n"
    "请调用 create_worldbuilding_entry 函数提交设定条目。"
)


def build_worldbuilding_writer_messages(
    *,
    style_context: str,
    world_context: str,
    requirements: str = "",
    dimension: str = "",
    title_hint: str = "",
) -> list[dict[str, str]]:
    """Build messages for worldbuilding entry generation."""
    anti_ai_rules = build_anti_ai_system_prompt()
    dim_guidance = DIMENSION_GUIDANCE.get(dimension, "")

    system_prompt = (
        f"{WORLDBUILDING_WRITER_SYSTEM_BASE}\n\n"
        f"{anti_ai_rules}\n\n"
        f"【风格设定】\n{style_context}"
    )
    if dim_guidance:
        system_prompt += f"\n\n{dim_guidance}"

    user_parts: list[str] = []
    if requirements:
        user_parts.append(f"【用户要求】\n{requirements}\n")
    if title_hint:
        user_parts.append(f"建议标题：{title_hint}")
    if dimension:
        user_parts.append(f"维度：{dimension}")
    user_parts.append(f"【已有世界观设定】\n{world_context}")
    user_parts.append("\n请创建世界观设定条目。")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
