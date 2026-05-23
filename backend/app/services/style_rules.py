"""Project writing style rules and forbidden sentence repair."""
from __future__ import annotations

import re
from typing import Optional

from ..ai.gateway import LLMGateway
from ..database.models import Project


STYLE_OPTIONS = ["vivid", "concise", "serious", "humorous", "poetic"]
STYLE_PROMPTS = {
    "vivid": "请用生动形象、富有画面感的语言改写。要求：优先使用具体动作、感官细节和场景调度制造画面感；不要依赖密集比喻或华丽排比；将抽象概括转化为具体场景。",
    "concise": "请用简洁精炼的语言改写，去除冗余。要求：删除重复表述和空洞修饰词；合并可归并的句子；用精准动词和名词替代冗长形容结构；提高信息密度。",
    "serious": "请用严肃庄重的语言改写。要求：句式规整，避免口语化和俏皮话；用词精准克制，不夸张不煽情；保持客观冷静的叙事距离。",
    "humorous": "请用幽默诙谐的语言改写。要求：可运用反讽、夸张、反差、双关等手法；节奏轻快；幽默应为角色和剧情服务，而非单纯搞笑。",
    "poetic": "请用富有诗意的语言改写。要求：注重语句的韵律感和节奏美；善用意象和留白；情感含蓄有层次，避免直白抒情。",
}

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
)

FORBIDDEN_SENTENCE_REGEXES = {
    "不是……是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[，,、\s]*是[^。！？!?；;\n]{1,80}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*是[^。！？!?；;\n]{1,80}",
    ],
    "不是……而是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,120}而是[^。！？!?；;\n]{1,120}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*而是[^。！？!?；;\n]{1,80}",
    ],
    "不是……却是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,120}却是[^。！？!?；;\n]{1,120}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*却是[^。！？!?；;\n]{1,80}",
    ],
    "与其说……不如说……": [
        r"与其说[\s\S]{1,120}?不如说[\s\S]{1,120}?",
    ],
    "在……中……": [
        r"在[^。；，]{2,30}中[，,\s]*[^。]{2,60}",
    ],
    "在……时……": [
        r"在[^。；，]{2,30}时[，,\s]*[^。]{2,60}",
    ],
    "随着……": [
        r"[，。！？\n]随着[^。]{5,80}",
        r"^随着[^。]{5,80}",
    ],
    "仿佛……": [
        r"仿佛[^。！？]{4,60}",
    ],
    "似乎……": [
        r"似乎[^。！？]{4,60}",
    ],
    "只见……": [
        r"只见[^。]{2,60}",
    ],
    "只听得……": [
        r"只听得[^。]{2,60}",
    ],
    "不由得……": [
        r"不由得[^。]{2,40}",
    ],
    "不禁……": [
        r"不禁[^。]{2,40}",
    ],
    "忍不住……": [
        r"忍不住[^。]{2,40}",
    ],
    "这一切都说明……": [
        r"这一切都说明[^。]{2,80}",
    ],
    "从那天起……": [
        r"从那天起[^。]{2,80}",
    ],
    "此后……": [
        r"此后[，,][^。]{2,80}",
        r"此后[^。]{2,60}",
    ],
    "与此同时……": [
        r"与此同时[，,][^。]{2,80}",
        r"与此同时[^。]{2,60}",
    ],
    "另一方面……": [
        r"另一方面[，,][^。]{2,80}",
    ],
    "很愤怒": [
        r"很愤怒",
    ],
    "感到悲伤": [
        r"感到悲伤",
    ],
    "感到恐惧": [
        r"感到恐惧",
    ],
    "显得很……": [
        r"显得很[^。]{1,30}",
    ],
    "他的眼中……": [
        r"他的眼中[^。]{2,60}",
        r"她的眼中[^。]{2,60}",
    ],
    "她的心里……": [
        r"她的心里[^。]{2,60}",
        r"他的心里[^。]{2,60}",
    ],
    "深深地": [
        r"深深地[^。]{1,30}",
    ],
    "无比": [
        r"无比[^。]{1,30}",
    ],
    "极其": [
        r"极其[^。]{1,30}",
    ],
    "一股……": [
        r"一股[^。]{1,30}",
    ],
    "一种……的感觉": [
        r"一种[^。]{1,30}的感觉",
    ],
    "令人……": [
        r"令人[^。]{1,20}",
    ],
    "让人……": [
        r"让人[^。]{1,20}",
    ],
    "充满了": [
        r"充满了[^。]{1,30}",
    ],
    "充斥着": [
        r"充斥着[^。]{1,30}",
    ],
    "缓缓地": [
        r"缓缓地",
    ],
    "默默地": [
        r"默默地",
    ],
    "静静地": [
        r"静静地",
    ],
    "淡淡地": [
        r"淡淡地",
    ],
    "微微……": [
        r"微微[一][^。]{1,20}",
        r"微微[^一。]{1,10}",
    ],
    "然而": [
        r"[，。！？\n]然而[^。]{2,60}",
        r"^然而[^。]{2,60}",
    ],
    "于是": [
        r"[，。！？\n]于是[^。]{2,60}",
    ],
    "突然": [
        r"突然[^。]{2,50}",
    ],
    "忽然": [
        r"忽然[^。]{2,50}",
    ],
    "终于": [
        r"终于[^。]{2,40}",
    ],
    "其实": [
        r"[，。！？\n]其实[^。]{2,60}",
    ],
    "总之": [
        r"总之[，,][^。]{2,60}",
    ],
    "无论如何": [
        r"无论如何[^。]{2,60}",
    ],
    "毋庸置疑": [
        r"毋庸置疑[^。]{2,60}",
    ],
    "某种程度上": [
        r"某种程度上[^。]{2,60}",
    ],
    "某种意义上": [
        r"某种意义上[^。]{2,60}",
    ],
}



def effective_forbidden_patterns(project: Project) -> str:
    """Merge system defaults with user-customized forbidden patterns."""
    default_patterns = {line.strip() for line in DEFAULT_FORBIDDEN_SENTENCE_PATTERNS.splitlines() if line.strip()}
    user_forbidden = (project.forbidden_sentence_patterns or "").strip()
    user_patterns = {line.strip() for line in user_forbidden.splitlines() if line.strip()} if user_forbidden else set()
    merged = default_patterns | user_patterns
    return "\n".join(sorted(merged, key=lambda x: (x not in default_patterns, x)))


def effective_rhetoric_guidelines(project: Project) -> str:
    """Append system default rhetoric guidelines to user's custom ones."""
    user_rhetoric = (project.rhetoric_guidelines or "").strip()
    if user_rhetoric:
        return f"{user_rhetoric}\n{DEFAULT_RHETORIC_GUIDELINES}"
    return DEFAULT_RHETORIC_GUIDELINES


def _build_style_context(project: Project) -> str:
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
    forbidden_patterns = effective_forbidden_patterns(project)
    rhetoric_guidelines = effective_rhetoric_guidelines(project)
    parts = [f"叙事视角：{perspective}", f"文风偏好：{style}"]
    # Lens-based narration rules — the most fundamental constraint
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
    # Anti-AI-flavor core rules
    parts.append(
        "去AI味硬规则：你写的是中文通俗小说，不是作文、不是论文、不是新闻稿。"
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
    if getattr(project, "short_sentences", False):
        parts.append(
            "短句模式（硬性要求）：以短句为主，平均句长控制在15-25字。"
            "避免多层从句嵌套；一个句子只讲一件事。多用句号，少用逗号连接多个分句。"
            "人物对白用简短口语，不要写长篇独白。叙事句优先主谓宾结构。"
        )
    if forbidden_patterns:
        patterns = [line.strip() for line in forbidden_patterns.splitlines() if line.strip()]
        if patterns:
            parts.append("禁用句式：\n" + "\n".join(f"- {pattern}" for pattern in patterns))
            parts.append("生成或改写时必须主动避开上述句式，包括同义变体和近似模板。")
            parts.append(
                "硬性句式检查：交付前必须自查并改掉所有禁用句式。"
                "跨句变体也禁止，例如“不是A。是B。”、“不是A，而是B。”、“与其说A，不如说B。”。"
            )
    if rhetoric_guidelines:
        parts.append(f"修辞限制：{rhetoric_guidelines}")
    custom = (project.custom_style_prompt or "").strip()
    if custom:
        parts.append(f"【用户自定义风格要求 — 必须遵守】\n{custom}")
    return "\n".join(parts)


def _project_forbidden_patterns(project: Project) -> list[str]:
    raw = (project.forbidden_sentence_patterns or DEFAULT_FORBIDDEN_SENTENCE_PATTERNS).strip()
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _generic_forbidden_regex(pattern: str) -> Optional[str]:
    if "……" not in pattern:
        return None
    pieces = [piece for piece in pattern.split("……") if piece]
    if not pieces:
        return None
    return r"[\s\S]{0,80}?".join(re.escape(piece) for piece in pieces)


def _forbidden_snippet(text: str, start: int, end: int, radius: int = 24) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].replace("\n", "\\n")
    if left > 0:
        snippet = "..." + snippet
    if right < len(text):
        snippet += "..."
    return snippet


def _detect_forbidden_sentence_violations(text: str, project: Project) -> list[dict]:
    if not text:
        return []
    violations: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for pattern in _project_forbidden_patterns(project):
        regexes = FORBIDDEN_SENTENCE_REGEXES.get(pattern, [])
        generic = _generic_forbidden_regex(pattern)
        if generic:
            regexes = [*regexes, generic]
        if not regexes and pattern in text:
            start = text.find(pattern)
            regexes = [re.escape(pattern)]
        for regex in regexes:
            for match in re.finditer(regex, text):
                key = (pattern, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                violations.append({
                    "pattern": pattern,
                    "snippet": _forbidden_snippet(text, match.start(), match.end()),
                    "start": match.start(),
                    "end": match.end(),
                })
                if len(violations) >= 20:
                    return violations
    return violations


def _strip_plain_text_response(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    return value


def _repair_token_budget(text: str, requested_max_tokens: Optional[int]) -> int:
    estimated = max(2048, int(len(text or "") * 1.8))
    if requested_max_tokens:
        estimated = max(estimated, requested_max_tokens)
    return min(24000, estimated)


def _mechanical_repair_forbidden_sentences(text: str) -> str:
    """Last-resort cleanup for the built-in contrast and AI-cliché templates."""

    def clean_tail(value: str) -> str:
        value = value.strip()
        return value[1:] if value.startswith("在") and len(value) > 1 else value

    def replace_not_is(match: re.Match) -> str:
        left = match.group("left").strip()
        right = clean_tail(match.group("right"))
        return f"{left}并非关键，关键在于{right}"

    def replace_rather(match: re.Match) -> str:
        left = match.group("left").strip()
        right = match.group("right").strip()
        return f"{left}这个判断不够准确，{right}更贴近当前情况"

    def remove_prefix(match: re.Match) -> str:
        return match.group("after") if match.group("after") else match.group(0)

    rules = [
        # Original contrast patterns
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,80})[，,、\s]*是(?P<right>[^。！？!?；;\n]{1,80})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,80})[。！？!?；;]\s*是(?P<right>[^。！？!?；;\n]{1,80})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,120})而是(?P<right>[^。！？!?；;\n]{1,120})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,120})却是(?P<right>[^。！？!?；;\n]{1,120})",
            replace_not_is,
        ),
        (
            r"与其说(?P<left>[\s\S]{1,120}?)不如说(?P<right>[\s\S]{1,120}?)",
            replace_rather,
        ),
        # AI cliché repairs — strip filler prefixes
        (
            r"只见(?P<after>[^。！？!?\n]{2,})",
            lambda m: m.group("after").strip(),
        ),
        (
            r"只听得(?P<after>[^。！？!?\n]{2,})",
            lambda m: m.group("after").strip(),
        ),
        (
            r"不由得(?P<after>[^。！？!?\n]{2,})",
            lambda m: f"暗自{m.group('after').strip()}",
        ),
        (
            r"不禁(?P<after>[^。！？!?\n]{2,})",
            lambda m: f"默默{m.group('after').strip()}",
        ),
        # Strip omniscient transitions
        (
            r"这一切都说明[，,]?\s*",
            lambda m: "",
        ),
        (
            r"从那天起[，,]?\s*",
            lambda m: "",
        ),
        (
            r"与此同时[，,]?\s*",
            lambda m: "",
        ),
        (
            r"另一方面[，,]?\s*",
            lambda m: "",
        ),
        # Defuse emotion labels
        (
            r"很愤怒",
            lambda m: "攥紧了拳头",
        ),
        (
            r"感到悲伤",
            lambda m: "喉头发紧",
        ),
        (
            r"感到恐惧",
            lambda m: "后背发凉",
        ),
        (
            r"显得很(?P<after>[^。，,]{1,10})",
            lambda m: f"{m.group('after').strip()}",
        ),
        # Defuse eye-of-god framing
        (
            r"[他她]的眼中[，,]?\s*",
            lambda m: "",
        ),
        (
            r"[他她]的心里[，,]?\s*",
            lambda m: "",
        ),
    ]
    repaired = text
    for regex, replacer in rules:
        repaired = re.sub(regex, replacer, repaired)
    return repaired


async def _repair_forbidden_sentence_text(
    text: str,
    project: Project,
    model: Optional[str],
    max_tokens: Optional[int] = None,
) -> tuple[str, list[dict], list[dict]]:
    """Rewrite text only when it violates project-level forbidden sentence rules."""
    before = _detect_forbidden_sentence_violations(text, project)
    if not before:
        return text, [], []

    repaired = text
    remaining = before
    patterns = _project_forbidden_patterns(project)
    for _attempt in range(2):
        hit_list = "\n".join(
            f"- {item['pattern']}：{item['snippet']}" for item in remaining[:12]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是小说正文句式审校器。你的任务只做一件事："
                    "在不改变剧情事实、角色行动、信息顺序、叙事视角和语气的前提下，"
                    "删除或改写命中的禁用句式。"
                    "不要解释，不要加标题，不要输出清单，只输出修订后的完整正文。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "禁用句式如下，包含跨句变体也禁止：\n"
                    + "\n".join(f"- {pattern}" for pattern in patterns)
                    + "\n\n已经命中的片段：\n"
                    + hit_list
                    + "\n\n请修订下面全文。要求：保留原有剧情、人物、设定和段落顺序；"
                    "只把命中的句式改成普通因果、递进或判断句；避免大量比喻。\n\n"
                    f"{repaired}"
                ),
            },
        ]
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=_repair_token_budget(repaired, max_tokens),
            retry=1,
        )
        candidate = _strip_plain_text_response(result.get("content", ""))
        if candidate:
            repaired = candidate
        remaining = _detect_forbidden_sentence_violations(repaired, project)
        if not remaining:
            break
    if remaining:
        repaired = _mechanical_repair_forbidden_sentences(repaired)
        remaining = _detect_forbidden_sentence_violations(repaired, project)
    return repaired, before, remaining


async def _repair_assistant_parsed_style(
    parsed: dict,
    project: Project,
    model: Optional[str],
    max_tokens: Optional[int] = None,
) -> list[dict]:
    """Repair visible assistant reply and generated chapter draft fields in-place."""
    reports: list[dict] = []

    async def repair_field(owner: dict, key: str, field_name: str) -> None:
        value = str(owner.get(key) or "")
        if not value.strip():
            return
        repaired, before, remaining = await _repair_forbidden_sentence_text(value, project, model, max_tokens)
        if before:
            owner[key] = repaired
            reports.append({
                "field": field_name,
                "fixed": not remaining,
                "violations": before[:8],
                "remaining": remaining[:8],
            })

    await repair_field(parsed, "reply", "reply")
    draft = parsed.get("chapter_draft")
    if isinstance(draft, dict):
        await repair_field(draft, "content", "chapter_draft.content")
        await repair_field(draft, "summary", "chapter_draft.summary")
    return reports

