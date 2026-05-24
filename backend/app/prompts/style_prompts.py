"""Style prompt builders — assembles project style context for LLM prompts."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..database.models import Project


STYLE_REPAIR_SYSTEM = (
    "你是小说正文句式审校器。你的任务只做一件事："
    "在不改变剧情事实、角色行动、信息顺序、叙事视角和语气的前提下，"
    "删除或改写命中的禁用句式。"
    "不要解释，不要加标题，不要输出清单，只输出修订后的完整正文。"
)


def build_style_repair_messages(
    repaired_text: str,
    patterns: list[str],
    hit_details: str,
) -> list[dict]:
    hit_list = "\n".join(
        f"- {item['pattern']}：{item['snippet']}" for item in (
            [] if not hit_details else []
        )[:12]
    ) or hit_details
    return [
        {"role": "system", "content": STYLE_REPAIR_SYSTEM},
        {
            "role": "user",
            "content": (
                "禁用句式如下，包含跨句变体也禁止：\n"
                + "\n".join(f"- {pattern}" for pattern in patterns)
                + "\n\n已经命中的片段：\n"
                + hit_list
                + "\n\n请修订下面全文。要求：保留原有剧情、人物、设定和段落顺序；"
                "只把命中的句式改成普通因果、递进或判断句；避免大量比喻。\n\n"
                f"{repaired_text}"
            ),
        },
    ]


DEFAULT_FORBIDDEN_SENTENCE_PATTERNS = "\n".join([
    "不是……是……",
    "不是……而是……",
    "不是……却是……",
    "与其说……不如说……",
    "在……中……",
    "在……时……",
    "随着……",
    "仿佛……",
    "似乎……",
    "只见……",
    "只听得……",
    "不由得……",
    "不禁……",
    "忍不住……",
    "这一切都说明……",
    "从那天起……",
    "此后……",
    "与此同时……",
    "另一方面……",
    "很愤怒",
    "感到悲伤",
    "感到恐惧",
    "显得很……",
    "他的眼中……",
    "她的心里……",
    "深深地",
    "无比",
    "极其",
    "一股……",
    "一种……的感觉",
    "令人……",
    "让人……",
    "充满了",
    "充斥着",
    "缓缓地",
    "默默地",
    "静静地",
    "淡淡地",
    "微微……",
    "然而",
    "于是",
    "突然",
    "忽然",
    "终于",
    "其实",
    "总之",
    "无论如何",
    "毋庸置疑",
    "某种程度上",
    "某种意义上",
    "彰显",
    "诠释",
    "赋能",
    "映射",
    "折射",
    "油然而生",
    "心潮澎湃",
    "这一刻",
    "宛如",
    "格外",
    "分外",
    "由此可见",
    "总而言之",
    "值得注意的是",
    "不难发现",
])

DEFAULT_RHETORIC_GUIDELINES = (
    "克制使用比喻、拟人、排比等修辞，禁止连续堆叠比喻。"
    "优先用具体动作、感官细节、因果推进和角色反应来表达画面与情绪。"
    "非必要不使用抽象概念比喻；同一段落不要出现多个比喻。"
    "禁止以下AI模型高频套话：'仿佛在诉说着什么'、'似乎预示着什么'、'一股莫名的…'、"
    "'在那一刻仿佛…'、'内心涌起一股…'。"
    "禁止用'显得''表现出''呈现出'等外部观察动词代替描写——直接写角色的具体言行。"
    "禁止用'进行''展开''发起'等虚动词代替具体动作。"
    "禁止情感标签：不要出现'很愤怒''感到悲伤''充满恐惧''显得紧张'——用角色的身体反应和具体行动代替。"
    "禁止旁白式过渡：不要写'这一切都说明''从那天起''此后''与此同时'。"
    "禁止高频AI虚词：'彰显''诠释''赋能''映射''折射''不禁''油然而生''心潮澎湃''这一刻''仿佛''宛如'。"
    "禁止四字成语堆叠：同一句中连续出现2个及以上四字成语即视为堆砌，必须拆散。"
    "禁止句式单一：连续三句及以上使用相同主语开头（如'他做了A。他做了B。他做了C。'），必须变换句式。"
    "禁止'的'字泛滥：每句最多出现2个'的'字，超过必须拆分或改写。"
    "禁止概括性结尾：段落末句不得出现'总而言之''总而言之''由此可见'等总结词。"
    "禁止滥用程度副词：'非常''极其''无比''深深地''格外''分外'等程度副词每500字最多出现1次。"
)


def effective_forbidden_patterns(project: "Project") -> str:
    """Merge system defaults with user-customized forbidden patterns."""
    default_patterns = {line.strip() for line in DEFAULT_FORBIDDEN_SENTENCE_PATTERNS.splitlines() if line.strip()}
    user_forbidden = (project.forbidden_sentence_patterns or "").strip()
    user_patterns = {line.strip() for line in user_forbidden.splitlines() if line.strip()} if user_forbidden else set()
    merged = default_patterns | user_patterns
    return "\n".join(sorted(merged, key=lambda x: (x not in default_patterns, x)))


def effective_rhetoric_guidelines(project: "Project") -> str:
    """Append system default rhetoric guidelines to user-custom ones."""
    user_rhetoric = (project.rhetoric_guidelines or "").strip()
    if user_rhetoric:
        return f"{user_rhetoric}\n{DEFAULT_RHETORIC_GUIDELINES}"
    return DEFAULT_RHETORIC_GUIDELINES


def build_style_context(
    project: "Project",
    *,
    include_anti_ai: bool = True,
    concise: bool = False,
) -> str:
    perspective_map = {
        "first_person": "第一人称",
        "third_person": "第三人称",
        "omniscient": "上帝视角",
    }
    style_map = {
        "natural": "自然",
        "vivid": "华丽生动",
        "concise": "白描简洁",
        "serious": "严肃",
        "humorous": "幽默",
        "poetic": "诗意",
    }
    perspective = perspective_map.get(project.narrative_perspective, "第三人称")
    style = style_map.get(project.writing_style, "自然")
    parts = [f"叙事视角：{perspective}", f"文风偏好：{style}"]

    if concise:
        # Agent only needs routing-relevant info, not writing craft rules
        if getattr(project, "short_sentences", False):
            parts.append("短句模式：已启用")
        custom = (project.custom_style_prompt or "").strip()
        if custom:
            parts.append(f"用户自定义风格要求：{custom}")
        return "\n".join(parts)

    # Short sentence mode — placed first for maximum impact on all writer tools
    if getattr(project, "short_sentences", False):
        parts.append(
            "【短句模式 — 全局生效】以短句为主，平均句长控制在15-25字。"
            "避免多层从句嵌套；一个句子只讲一件事。多用句号，少用逗号连接多个分句。"
            "人物对白用简短口语，不要写长篇独白。叙事句优先主谓宾结构。"
            "本条优先级高于其他风格偏好。"
        )
    # Hard style constraints — top priority
    forbidden_patterns = effective_forbidden_patterns(project)
    if forbidden_patterns:
        patterns = [line.strip() for line in forbidden_patterns.splitlines() if line.strip()]
        if patterns:
            parts.append("【禁用句式 — 全局硬约束】\n" + "\n".join(f"- {pattern}" for pattern in patterns))
            parts.append("生成或改写时必须主动避开上述句式，包括同义变体和近似模板。")
    # Anti-AI-flavor core rules — skipped when include_anti_ai=False (chapter_writer loads full version)
    if include_anti_ai:
        parts.append(
            "【去AI味硬规则 — 全局生效】你写的是中文通俗小说，不是作文、不是论文、不是新闻稿。"
            "严禁以下AI模型高频语言习惯："
            "（1）禁用'在……中/时/后'句式开头的长状语——拆成独立短句或用动作承接；"
            "（2）禁用'随着……'开头——直接切进场景和动作；"
            "（3）禁用'仿佛''似乎''好像'等模糊化修饰——态度要确定，不要模棱两可；"
            "（4）禁用'只见''只听得''只感觉'等古典说书套话；"
            "（5）禁用'不由得''不禁''忍不住'等自动反应——改写成具体动作或内心独白；"
            "（6）禁用'进行''展开''发起'等虚动词——用精确动词替换；"
            "（7）禁止元评论：不要出现'可以说''不得不说''值得一说的'等写作者视角的点评；"
            "（8）禁止概括性总结句——如'这一切都说明……''从那天起……''此后……'，直接把场景切到下一幕，不用旁白过渡；"
            "（9）禁止情感标签——不要写'他很愤怒''她感到悲伤'，用动作、表情、呼吸、对话来呈现情绪；"
            "（10）禁止外貌堆砌——不要一次性描述角色的完整外貌，分散在动作和互动中逐步带出，且只写当前镜头能自然观察到的那部分；"
            "（11）禁止装饰性细节——不要为了'画面感'而添加无用的环境描写、感官填充、或气氛渲染。"
            "每一句环境描写都必须直接服务于当前场景的功能需求（暗示危险、反映角色心境、提供关键信息），否则删掉；"
            "（12）禁止连续动作分解——不要把一个简单动作拆成多个步骤。'他走向门口'，不要写'他站起身，迈开步子，穿过房间，来到门前'。"
        )
    # Lens-based narration rules
    parts.append(
        "镜头叙事规则（铁律）：你的叙述镜头必须始终锁定在场景主要角色的五感范围内。"
        "只写这个角色当下能看到、听到、闻到、触到、感受到的东西。"
        "（1）禁止跳到其他角色的内心——你不在谁的脑子里，就不能写谁的想法和感受。"
        "（2）禁止上帝视角交代背景——如果角色当下不知道某件事，读者也不能知道。"
        "（3）禁止描写角色视线之外发生的事——没有'与此同时'，没有镜头切走。"
        "（4）禁止装饰性环境描写——不要写'阳光透过树叶''微风吹过''空气中有淡淡的花香'之类与剧情无关的感官填充。"
        "天气、光线、温度只在影响角色行动或情绪转折时才能写，且不超过一句。"
        "（5）禁止外貌和服饰堆砌——角色出场时不要从头发写到鞋子。只需一个标志性特征。其余在后续动作中零散带出。"
        "（6）禁止分解动作——不要'他伸出手，握住门把，转动，然后推开'。只写'他推开门'。"
        "不推动剧情的细节一律删除。描写必须同时完成三件事之一：推动剧情、揭示角色、制造紧张。三者都不占的句子砍掉。"
    )
    rhetoric_guidelines = effective_rhetoric_guidelines(project)
    if rhetoric_guidelines:
        parts.append(f"修辞限制：{rhetoric_guidelines}")
    custom = (project.custom_style_prompt or "").strip()
    if custom:
        parts.append(f"【用户自定义风格要求 — 必须遵守】\n{custom}")
    return "\n".join(parts)
