"""Character Writer prompt — assembles character crafting rules for card generation."""
from __future__ import annotations

from .anti_ai_prompts import build_anti_ai_system_prompt

CHARACTER_CRAFT_RULES = (
    "【角色设计原则】\n"
    "1. 角色必须有缺陷——完美角色是无聊的。每个角色至少有一个内在矛盾（欲望vs恐惧、信念vs行为）。\n"
    '2. 角色必须主动——即使是被动型角色，也要写出他在选择"不行动"时的内心挣扎。\n'
    '3. 角色的性格不是标签的堆砌——"高冷""温柔""腹黑"只是起点。写出性格在不同情境下的具体表现。\n'
    "4. 外貌描写服务于性格和剧情——一个特征说清角色本质。不要罗列。\n"
    '5. 背景故事解释"为什么"——角色的过去如何塑造了他现在的行为模式？\n'
    "6. 能力有代价——每个能力/技能都应该有对应的限制、弱点或代价。没有制衡的能力是空洞的。\n"
    "7. 角色有成长空间——写出角色当前的状态和潜在的发展方向。让读者能看到角色的变化弧线。\n\n"
    "【角色类型参考】\n"
    "- protagonist（主角）：故事的核心视角，必须有清晰的目标、内在矛盾和成长弧线。\n"
    "- antagonist（反派）：与主角目标对立，但必须有合理的动机。最好的反派认为自己是对的。\n"
    "- supporting（配角）：服务于主角的成长或剧情的推进。有独立于主角的欲望。\n"
    "- mentor（导师）：提供指导、知识或工具，但自身也有局限和故事。\n"
    "- other（其他）：功能性角色，确保其存在对剧情是必要的。\n\n"
    "【角色深度检查清单 — 交付前自查】\n"
    "- 这个角色想要什么？（外显欲望）\n"
    "- 这个角色真正需要什么？（内在需求，他自己可能不知道）\n"
    "- 如果把他放到一个他不熟悉的环境中，他会怎么做？\n"
    "- 他和周围角色的关系是否有张力？\n"
    "- 去掉这个角色，故事会塌掉吗？如果会，为什么？\n\n"
    "【常见角色设计错误 — 必须避免】\n"
    "- 玛丽苏/龙傲天：能力无上限，性格无缺陷，所有人围着转。\n"
    "- 工具人：角色存在的唯一意义是帮主角推进剧情，没有自己的欲望。\n"
    '- 标签人：只有"高冷""温柔"这种空洞标签，没有具体行为支撑。\n'
    '- 矛盾人：行为与设定自相矛盾（如"聪明绝顶"的角色反复犯低级错误）。\n'
    "- 复制人：多个角色性格雷同，换个名字就是同一个人。\n"
)

CHARACTER_WRITER_SYSTEM_BASE = (
    "你是一位资深角色设计师，专精于创造立体、真实、有记忆点的小说角色。\n"
    "你不会给出笼统的人物模板——你创造的每个角色都是独一无二的个体。\n\n"
    "【任务】\n"
    "根据提供的项目上下文和用户需求，创建一份完整的角色卡片。\n"
    "如果用户要求创建多个角色，确保每个角色的性格、动机和说话方式有明显区分。\n\n"
    "请调用 create_character 函数提交角色卡片。"
)


def build_character_writer_messages(
    *,
    style_context: str,
    world_context: str,
    existing_characters: str,
    requirements: str = "",
    name_hint: str = "",
    role_hint: str = "",
) -> list[dict[str, str]]:
    """Build messages for character card generation."""
    anti_ai_rules = build_anti_ai_system_prompt()

    system_prompt = (
        f"{CHARACTER_WRITER_SYSTEM_BASE}\n\n"
        f"{CHARACTER_CRAFT_RULES}\n\n"
        f"{anti_ai_rules}\n\n"
        f"【风格设定】\n{style_context}"
    )

    user_parts: list[str] = []
    if requirements:
        user_parts.append(f"【用户要求】\n{requirements}\n")
    if name_hint:
        user_parts.append(f"角色名：{name_hint}")
    if role_hint:
        user_parts.append(f"建议角色类型：{role_hint}")
    user_parts.append(f"【世界观背景】\n{world_context}")
    if existing_characters and existing_characters != "暂无角色。":
        user_parts.append(f"【已有角色（新角色必须与已有角色有区分度）】\n{existing_characters}")
    user_parts.append("\n请创建角色卡片。")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
