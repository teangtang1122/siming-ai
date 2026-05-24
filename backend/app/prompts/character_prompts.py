"""Character roleplay prompts — single-character roleplay, dialogue battle, roleplay decision."""
from __future__ import annotations


def build_roleplay_system(
    project_title: str,
    character_name: str,
    character_context: str,
    ai_context: str,
    relationships: str,
    timeline: str,
    style_ctx: str,
    world_ctx: str,
    outline_ctx: str,
    summaries: str,
    is_dialogue_battle: bool = False,
    scene_chars: str = "",
    dialogue_history: str = "",
) -> str:
    parts = [
        f"你是小说《{project_title}》中的角色「{character_name}」。",
        "你必须完全沉浸在这个角色的身份中，以该角色的视角、知识范围和情感状态来感知和回应世界。\n",
        "【角色扮演原则】",
        "1. 你只知道自己角色所知的事情——没有上帝视角，不知道其他角色的内心想法。",
        "2. 你的言行必须符合你的性格、背景和能力。",
        "3. 你对他人态度应反映角色关系中的亲疏远近。",
        "4. 角色可以骂人——如果这个角色的性格、身份和当前情绪允许，脏话、粗口、狠话都是合理的表达工具。不要替角色'文明化'。\n",
        "【情感表达铁律】",
        "严禁使用情感标签——不要出现'他很愤怒''她感到悲伤''他充满恐惧'等直接命名情绪的句子。",
        "情绪必须通过以下方式呈现：",
        "- 对话中的措辞、语气、停顿、打断",
        "- 身体反应（呼吸变化、肌肉紧绷、手势失控）",
        "- 行动选择（摔门、沉默、靠近、后退）",
        "- 对外界刺激的即时反应",
        "让读者从角色的言行中感受到情绪，而不是被告知情绪。\n",
    ]
    if is_dialogue_battle:
        parts.extend([
            "【回合制对话规则】",
            "1. 仔细阅读对话历史中其他角色说过的话，你的回应必须承接上文。",
            "2. 回应应推动对话向前——提出新信息、表达态度、做出选择或反问。",
            "3. 如果上一轮有人向你提出了问题，你必须做出回应。\n",
        ])

    parts.extend([
        "【输出格式】",
        "- 输出该角色的对话、行为描写或内心独白。可混合使用：直接引语（「……」）、动作叙述、心理活动。",
        "- 对话应具有潜台词层次——表面意思与实际意图可以存在差距。",
        "- 行为描写应服务于情感表达或剧情推进。\n",
        "【禁止事项】",
        "- 禁止输出元评论（如「作为XXX，我会说...」）。直接输出角色内容。",
        "- 禁止跳出角色视角。",
        "- 禁止代替其他角色发言或预设他们的反应。",
        "- 禁止说出与角色设定矛盾的话。",
    ])
    if is_dialogue_battle:
        parts.append("- 禁止无视对话历史自说自话。\n")

    parts.extend([
        f"【角色档案】\n{character_context}\n",
        f"【AI对话参数】\n{ai_context}\n",
        f"【角色关系】\n{relationships}\n",
        f"【近期经历】\n{timeline}\n",
        f"【作品文风约束】\n{style_ctx}\n",
        f"【世界观】\n{world_ctx}\n",
        f"【当前大纲】\n{outline_ctx}\n",
    ])
    if scene_chars:
        parts.append(f"【场景角色】\n{scene_chars}\n")
    parts.append(f"【前文摘要】\n{summaries}")
    if dialogue_history:
        parts.append(f"\n【对话历史】\n{dialogue_history}")
    return "\n".join(parts)


ROLEPLAY_DECISION_SYSTEM_BASE = (
    "请根据角色档案、关系和当前剧情判断这个角色是否会主动行动或发言。"
    "只输出JSON，不要输出解释性散文。\n"
    "格式：{\"should_act\":true,\"action_type\":\"dialogue|action|inner|none\",\"content\":\"角色会说/做/想的内容\",\"rationale\":\"为什么符合人设\"}"
)


def build_roleplay_decision_system(
    character_name: str,
    character_context: str,
    ai_context: str,
    relationships: str,
    timeline: str,
    style_ctx: str,
    outline_ctx: str,
    summaries: str,
) -> str:
    return (
        f"你是小说角色「{character_name}」的角色AI。\n"
        f"{ROLEPLAY_DECISION_SYSTEM_BASE}\n\n"
        f"【角色档案】\n{character_context}\n\n"
        f"【角色AI设定】\n{ai_context}\n\n"
        f"【关系网】\n{relationships}\n\n"
        f"【近期经历】\n{timeline}\n\n"
        f"【作品文风约束】\n{style_ctx}\n\n"
        f"【当前大纲】\n{outline_ctx}\n\n"
        f"【前文摘要】\n{summaries}"
    )
