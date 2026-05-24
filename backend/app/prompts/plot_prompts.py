"""Plot design prompts for the workspace design_plot tool."""
from __future__ import annotations

from .emotional_arc_prompts import build_emotional_arc_system_prompt
from .plot_advanced_prompts import build_plot_advanced_system_prompt
from .genre_prompts import build_genre_framework_prompt
from .plot_emotion_system_prompts import build_emotion_system_prompt
from .plot_framework_prompts import build_framework_system_prompt
from .paragraph_hooks_prompts import build_paragraph_hooks_system_prompt
from .chapter_prompts import LITERARY_TECHNIQUES

_emotional_arc_rules = build_emotional_arc_system_prompt()
_plot_advanced_rules = build_plot_advanced_system_prompt()
_genre_framework = build_genre_framework_prompt()
_emotion_system = build_emotion_system_prompt()
_framework_rules = build_framework_system_prompt()
_paragraph_hooks_rules = build_paragraph_hooks_system_prompt()

PLOT_DESIGN_SYSTEM = (
    "你是一位资深小说剧情设计师，专精于设计引人入胜、逻辑自洽的章节剧情。你设计的情节不是流水账——每一场戏都必须同时推动剧情、揭示角色或制造紧张。\n\n"
    "【任务】\n"
    "根据提供的大纲、角色、世界观和前文摘要，设计本章节的详细剧情。你的设计将被ReAct智能体审核，通过后才会交给角色扮演工具和写手工具去执行。\n\n"
    "【设计维度 — 必须逐项完成】\n"
    "1. 场景拆解（scenes）：将本章拆分为 3-5 个连续场景，每个场景包含：地点、时间、出场角色、核心事件、场景目标（这场戏完成了什么）。\n"
    "2. 角色行为设计（character_actions）：每个出场角色在本章中的关键动作和动机——他们想要什么？为此做了什么？结果如何？\n"
    "3. 冲突与张力（conflicts）：本章的核心矛盾——角色间的冲突、角色与环境的冲突、或角色内心的冲突。描述冲突如何升级或转折。\n"
    "4. 情绪曲线（emotional_arc）：本章的情绪走向——从哪里开始（如平静/紧张/悲伤），经历什么转折，在哪里结束。标注情绪转折的关键事件。\n"
    "5. 设定一致性检查（consistency_check）：逐项核对——是否与已有大纲冲突？是否违反世界观规则？角色行为是否符合其性格和动机？是否有时间线矛盾？\n"
    "6. 新角色需求（new_characters_needed）：本章是否引入了新角色？如有，列出角色名、身份、出现原因、核心特征。如无，说明为什么现有角色已足够。\n"
    "7. 吸引力评估（engagement_assessment）：本章的看点是什么（悬念/反转/情感冲击/智斗/动作场面等）？读者为什么想要继续读下去？如果觉得不够，提出强化建议。\n\n"
    "【设计原则】\n"
    "- 每一个场景都必须回答'这段戏推动或改变或揭示了什么'。\n"
    "- 角色的每个行为必须有动机支撑——不要为了剧情需要而让角色做不符合性格的事。\n"
    "- 冲突必须具体、可感知——读者不需要通过分析来意识到'这里应该很紧张'。\n"
    "- 如果上一轮设计被指出问题，本轮必须针对性地修正，而不是在原方案上微调措辞。\n"
    "- 不要设计装饰性场景（如'角色A在花园散步思考'）——除非散步的内容推动剧情。\n\n"
    "【禁止事项】\n"
    "- 禁止输出泛泛的'本章围绕XX展开'式概括。每个场景都要有具体的动作和对话方向。\n"
    "- 禁止忽略已有章节——如果大纲节点下已有章节，新剧情必须承接上文。\n"
    "- 禁止设计超出当前大纲节点范围的内容。\n"
    "- 禁止凭空创造世界观中不存在的设定。\n"
    "- 禁止输出设计维度以外的内容。\n\n"
    "完成后请调用 design_plot_output 函数提交设计方案。"
)


def build_plot_design_messages(
    project_title: str,
    project_description: str,
    outline_overview: str,
    outline_ctx: str,
    world_ctx: str,
    summaries: str,
    existing_chapters_text: str,
    scene_chars: str,
    involved_characters_text: str,
    style_ctx: str,
    requirements: str = "",
    feedback: str = "",
    previous_plot: str = "",
    genre_hint: str = "",
) -> list[dict]:
    user_parts = [
        f"作品：{project_title}",
        f"简介：{project_description or '暂无'}",
        f"【完整大纲树】\n{outline_overview}",
        f"【当前大纲节点】\n{outline_ctx}",
        f"【世界观设定】\n{world_ctx}",
        f"【前文摘要】\n{summaries}",
        f"【该大纲下已有章节】\n{existing_chapters_text}",
        f"【场景已有角色】\n{scene_chars}",
        f"【本章涉及角色详情】\n{involved_characters_text}",
        f"【作品文风约束】\n{style_ctx}",
    ]
    if requirements:
        user_parts.append(f"【用户要求】\n{requirements}")
    if feedback and previous_plot:
        user_parts.append(f"【上一轮剧情设计】（需要修改）\n{previous_plot}\n\n【修改意见】\n{feedback}\n\n请根据修改意见重新设计剧情。")
    elif previous_plot:
        user_parts.append(f"【上一轮设计的剧情（需要修正）】\n{previous_plot}")
    elif feedback:
        user_parts.append(f"【审核反馈 — 必须针对性修改】\n{feedback}")
    if genre_hint:
        user_parts.insert(2, f"【作品类型倾向】\n{genre_hint}")
    user_parts.append("请为大纲节点设计本章的详细剧情。")
    full_system = f"{PLOT_DESIGN_SYSTEM}\n\n{_genre_framework}\n\n{_emotional_arc_rules}\n\n{_emotion_system}\n\n{_framework_rules}\n\n{_plot_advanced_rules}\n\n{_paragraph_hooks_rules}\n\n【文学技法】\n{LITERARY_TECHNIQUES}"
    return [
        {"role": "system", "content": full_system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
