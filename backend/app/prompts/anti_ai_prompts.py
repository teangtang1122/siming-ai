"""Systematic AI-flavor detection and removal.

The public constants in this module are also used by project style settings, so
the rewrite prompt deliberately treats them as *diagnostic signals* instead of
blind replacement rules.  Mechanical synonym swaps tend to make long-form
fiction more uniform, which is both worse prose and a strong machine-writing
signal in its own right.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean, pstdev
from typing import Any

# ---------------------------------------------------------------------------
# TIER 1 BANNED WORDS — replace immediately when found
# ---------------------------------------------------------------------------
TIER1_BANNED_WORDS = {
    "modal":     ["仿佛", "犹如", "宛若", "一丝", "一抹", "些许", "几分", "隐约"],
    "action":    ["深吸一口气", "缓缓", "不禁", "微微", "轻轻", "淡淡"],
    "expression": ["眼中闪过", "嘴角勾起", "眉头微皱", "眉眼低垂", "瞳孔微缩"],
    "psych":     ["心中一动", "心头一震", "心下了然", "心中暗道", "心底泛起", "不由得"],
    "judgment":  ["不容置疑", "不易察觉", "显而易见", "毫无疑问", "不可否认"],
    "describe":  ["坚定", "闪烁着光芒", "狡黠", "深邃", "凛冽"],
    "transition": ["不由自主", "情不自禁", "自然而然"],
    "vague":     ["命运", "宿命", "注定", "潮水般", "如闪电般", "仿佛春风"],
}

# ---------------------------------------------------------------------------
# TIER 2 CONTEXT-SENSITIVE — replace only when overused
# ---------------------------------------------------------------------------
TIER2_THRESHOLD_WORDS = [
    "突然", "好像", "瞬间", "于是乎", "与此同时", "从而", "因而", "诚然",
]

# ---------------------------------------------------------------------------
# FORBIDDEN SENTENCE TEMPLATES
# ---------------------------------------------------------------------------
FORBIDDEN_SENTENCE_TEMPLATES = [
    ("「…，带着…」万能状语", "他说，带着一丝无奈"),
    ("陈词滥调/万能比喻", "像刀子一样锋利"),
    ("过度文艺声音描写", "他的声音很轻，却像…"),
    ("文言腔残留", "仿佛能…一般"),
    ("公式化对话标签", "好的，他说道（高频时）"),
    ("「他/她感到…」告知句式", "她感到一丝失落"),
    ("「他/她意识到…」直接告知", "他意识到事情不对"),
    ("「眼中闪过一丝XX」模板", "眼中闪过一丝悲伤"),
    ("「嘴角勾起一抹XX」模板", "嘴角勾起一抹冷笑"),
    ("「心中涌起一股XX」模板", "心中涌起一股暖流"),
]

# ---------------------------------------------------------------------------
# TIER 3 SENTENCE-LEVEL STRUCTURAL PATTERNS — ban the pattern, not the words
# ---------------------------------------------------------------------------
TIER3_SENTENCE_PATTERNS = [
    "不是……是……",
    "不是……而是……",
    "不是……却是……",
    "与其说……不如说……",
    "在……中……",
    "在……时……",
    "随着……",
    "只见……",
    "只听得……",
    "忍不住……",
    "这一切都说明……",
    "从那天起……",
    "此后……",
    "另一方面……",
    "显得很……",
    "他的眼中……",
    "她的心里……",
    "一种……的感觉",
    "令人……",
    "让人……",
    "充满了",
    "充斥着",
    "默默地",
    "静静地",
    "其实",
    "总之",
    "无论如何",
    "毋庸置疑",
    "某种程度上",
    "某种意义上",
    "由此可见",
    "总而言之",
    "值得注意的是",
    "不难发现",
]

# ---------------------------------------------------------------------------
# CHAPTER-END SUMMARY DETECTION — AI fingerprint patterns
# ---------------------------------------------------------------------------
CHAPTER_END_BAN_PATTERNS = [
    # Summary insight
    "他终于明白了", "她终于懂了", "他终于意识到", "她这才明白",
    "这一刻，他终于", "那一刻，她终于",
    # Grandeur升华
    "这一夜，注定无人入眠", "这一天，改变了一切",
    "从此，一切都不同了", "他的人生翻开了新的一页",
    # Philosophical
    "人生就是这样", "命运总是如此", "或许这就是",
    "生活教会了他", "时间会让你明白",
    # Preview预告
    "他不知道的是，更大的", "他不知道，等待他的将是",
    "他不知道，这一切才刚刚开始",
    "他不知道，更大的风暴即将来临",
]

# ---------------------------------------------------------------------------
# STACKED-WRITING DETECTION (堆叠式写作)
# ---------------------------------------------------------------------------
STACKED_WRITING_RULE = (
    "【堆叠式写作检测 — 同一瞬间被拆成三段】\n"
    "AI最常见的写作痕迹：先写概括动作 -> 再写感知细节 -> 再写身体反应，"
    "三段说的是同一个瞬间的事。\n"
    "检测特征：\n"
    "- 「发生层→感知层→反应层」按顺序分段出现\n"
    "- 同一动作被掰开写了三遍\n"
    "- 每一维度独立成段，而不是织入同一段正文\n\n"
    "正确做法：发生、感知、反应三个维度织入同一段连续正文：\n"
    "> 林父左手压着文书，右手拿笔往纸上落——笔尖一触纸面就偏了，"
    "从肘到腕止不住地抖，那一横斜着拖出去。\n"
    "处理原则：合并同一瞬间的重复描写，而非删除情绪细节。"
)

# ---------------------------------------------------------------------------
# 7 AI WRITING PATTERNS
# ---------------------------------------------------------------------------
AI_PATTERN_1_HIGH_FREQ_WORDS = (
    "【模式1：AI高频词】\n"
    "禁用词：不禁、仿佛/宛如、映入眼帘、心中暗道、沉声道/淡淡地说、"
    "脸色一变、嘴角微扬、不由自主、只见/此时此刻、目光如炬\n"
    "替换原则：\n"
    "- 「不禁」-> 删掉\n"
    "- 「仿佛/宛如」-> 删掉或用具体描写\n"
    "- 「心中暗道」-> 用动作展示思考\n"
    "- 「沉声道/淡淡地说」-> 换成动作标签\n"
    "- 「脸色一变」-> 用具体表情/动作\n"
    "- 「嘴角微扬」-> 他笑了/他翘了下嘴\n"
    "- 「只见/此时此刻」-> 删掉\n"
)

AI_PATTERN_2_WEAK_ADVERBS = (
    "【模式2：弱化副词泛滥】\n"
    "阈值：每1000字超过3个 = AI签名\n"
    "重点监控：微微、淡淡、缓缓、轻轻\n"
    "替换：将副词修饰改为具体的身体动作或状态描写。\n"
)

AI_PATTERN_3_MEANING_INFLATION = (
    "【模式3：意义膨胀】\n"
    "- 「意义深远」-> 写具体后果\n"
    "- 「前所未有」-> 给出对比参照\n"
    "- 「可谓」-> 删掉\n"
    "- 「令人震惊」-> 写围观者的具体反应\n"
)

AI_PATTERN_4_UNIVERSAL_CONCLUSION = (
    "【模式4：万能结论】\n"
    "- 「未来可期」-> 用未解决的紧张感结尾\n"
    "- 「前途无量」-> 删\n"
    "- 「充满希望」-> 写具体的下一步动作\n"
    "- 「一切尽在不言中」-> 删，用沉默和动作替代\n"
)

AI_PATTERN_5_ESSAY_STRUCTURE = (
    "【模式5：论文体段落结构】\n"
    "小说中出现以下开头句 = AI入侵：\n"
    "「不难看出」「由此可见」「事实上」「综上所述」\n"
    "替换：直接叙事，不要对叙事内容做分析总结。\n"
)

AI_PATTERN_6_FORMAL_CONJUNCTIONS = (
    "【模式6：书面语连词泛滥】\n"
    "叙事中频繁出现「于是乎」「与此同时」「从而」「因而」「诚然」\n"
    "-> 口语化替代或直接删除。\n"
)

AI_PATTERN_7_TRIPLE_PARALLEL = (
    "【模式7：三连排比癖】\n"
    "AI喜欢把事情凑成三个——「有的…有的…有的…」「一边…一边…一边…」\n"
    "-> 砍到只剩最有力的一条。\n"
)

ALL_AI_PATTERNS = "\n\n".join([
    AI_PATTERN_1_HIGH_FREQ_WORDS,
    AI_PATTERN_2_WEAK_ADVERBS,
    AI_PATTERN_3_MEANING_INFLATION,
    AI_PATTERN_4_UNIVERSAL_CONCLUSION,
    AI_PATTERN_5_ESSAY_STRUCTURE,
    AI_PATTERN_6_FORMAL_CONJUNCTIONS,
    AI_PATTERN_7_TRIPLE_PARALLEL,
])

# ---------------------------------------------------------------------------
# SYSTEMATIC 3-PASS DE-AI METHOD
# ---------------------------------------------------------------------------
DE_AI_PASS_1 = (
    "【Pass 1：去泛化（Strip Generic）— 去掉80%的AI味】\n"
    "1. 抽象情绪总结句 -> 删或替换为具体动作\n"
    "2. 假深度句 -> 删\n"
    "3. 意义膨胀 -> 缩小到具体影响\n"
    "4. 空洞结论 -> 删\n"
    "5. 工整对比句式 -> 打散重写\n"
    "6. 装饰性形容词堆砌 -> 白描\n"
    "7. 过度使用「于是」「然而」「此刻」-> 删掉一半\n"
    "8. 所有角色说话方式一样 -> 区分语气\n"
    "原则：能删就删，不能删就用具体细节替换。"
)

DE_AI_PASS_2 = (
    "【Pass 2：去书面化（Cut Professional Diction）】\n"
    "1. 分析性用词（「机制」「结构」「逻辑」「体系」出现在小说中）-> 换成日常表达\n"
    "2. 抽象名词滥用 -> 直接说事\n"
    "3. 体制内用语（「进一步」「深入」「推进」「落实」）-> 删\n"
    "4. 专业术语堆砌 -> 只保留必要的，用白话解释\n"
    "例外：历史题材正式用语、文学向刻意密度、喜剧夸张修辞可保留。"
)

DE_AI_PASS_3 = (
    "【Pass 3：回自然感（Restore Natural Presence）】\n"
    "1. 具体的感官细节（气味、温度、触感）\n"
    "2. 角色说话方式的区分（不同人不同语气）\n"
    "3. 节奏变化（长短句交错）\n"
    "4. 社会位置感的对话（上级和下属说话方式不同）\n"
    "5. 场景特有的记忆点\n"
    "6. 项目特有的语言习惯（角色的口头禅）\n"
    "原则：少即是多。每段加1-2个具体细节就够了。"
)

DE_AI_3_PASS_METHOD = "\n\n".join([
    "【系统性去AI三遍法】\n",
    DE_AI_PASS_1,
    "",
    DE_AI_PASS_2,
    "",
    DE_AI_PASS_3,
    "",
    "【升级策略】\n"
    "- 轻度AI味：只做Pass 1\n"
    "- 中度AI味：Pass 1 + Pass 2\n"
    "- 重度AI味：完整三遍 + 重点段落重写",
])

# ---------------------------------------------------------------------------
# SHOW-DON'T-TELL REPLACEMENT TABLE
# ---------------------------------------------------------------------------
EMOTION_REPLACEMENT_TABLE = (
    "【情绪外化 — Show Don't Tell 替换表】\n"
    "紧张：\n"
    "  ❌「他感到一阵紧张，心跳不由自主地加快了」\n"
    "  ✅「他攥紧了手里的纸杯，水洒出来一些」\n"
    "愤怒：\n"
    "  ❌「愤怒在他心中燃烧，他不由得握紧了拳头」\n"
    "  ✅「他把筷子往桌上一拍，碗里的汤溅了出来」\n"
    "悲伤：\n"
    "  ❌「一丝悲伤涌上心头，她的眼中闪过泪光」\n"
    "  ✅「她低头搅着咖啡，搅了很久」\n"
    "害怕：\n"
    "  ❌「恐惧瞬间笼罩了他，他感到一阵战栗」\n"
    "  ✅「他的背贴在墙上，不敢动」\n"
    "失望：\n"
    "  ❌「她感到一丝失落，心仿佛被什么东西揪住了」\n"
    "  ✅「『哦。』她把手机锁了屏」\n"
    "惊讶：\n"
    "  ❌「他的瞳孔微微收缩，显然没有想到会听到这样的话」\n"
    "  ✅「他张了张嘴，什么都没说出来」\n"
    "心痛：\n"
    "  ❌「一阵心痛袭来」\n"
    "  ✅「手指掐进肉里自己不知道疼」\n"
    "绝望：\n"
    "  ❌「他陷入了深深的绝望」\n"
    "  ✅「他坐在那里，烟灰掉了一裤腿也没有弹」\n"
    "心如死灰：\n"
    "  ❌「她心如死灰」\n"
    "  ✅「她把手机翻过来扣在桌上，再没拿起来过」\n"
)

# ---------------------------------------------------------------------------
# SCENE / ENDING REWRITE EXAMPLES
# ---------------------------------------------------------------------------
SCENE_REWRITE_EXAMPLES = (
    "【场景改写示例】\n"
    "AI风场景：\n"
    "  ❌「阳光透过窗帘的缝隙洒进来，在地板上投下斑驳的光影。"
    "空气中弥漫着淡淡的花香，仿佛整个世界都沉浸在一片宁静祥和的氛围中。」\n"
    "  ✅「下午三点，客厅里只有钟在走。」\n\n"
    "AI风打斗：\n"
    "  ❌「他的拳头犹如疾风骤雨般猛烈，每一击都蕴含着不容置疑的力量。"
    "对手的瞳孔微微收缩，显然没有预料到如此凌厉的攻势。」\n"
    "  ✅「他一拳怼过去，对方没躲开，嘴角破了。」\n\n"
    "结尾改写：\n"
    "  升华式 ❌「他站在窗前，望着远方的天际线，终于明白了生活的真谛」\n"
    "        ✅「他把烟掐了，回屋睡觉。」\n"
    "  总结式 ❌「这一刻，一切都变了。她知道，从今以后，她的人生将翻开崭新的一页。」\n"
    "        ✅「她关上了那扇门。没回头。」\n"
    "  感慨式 ❌「岁月如流水般悄然流逝……」\n"
    "        ✅ 直接删掉这种段落。\n"
)

# ---------------------------------------------------------------------------
# QUICK SELF-CHECK MNEMONIC
# ---------------------------------------------------------------------------
QUICK_SELF_CHECK = "\n".join([
    "一段不过三句话。",
    "对话要像人说话。",
    "心情不写心里话。",
    "结尾不搞大升华。",
    "打斗不写流水账。",
    "日常要埋伏笔桩。",
])

# ---------------------------------------------------------------------------
# INPUT-AWARE REVISION DIAGNOSTICS
# ---------------------------------------------------------------------------

# Only phrases that actually occur in the source are echoed into the rewrite
# prompt.  This avoids priming the model with pages of stock expressions that
# were not present in the chapter in the first place.
DE_AI_FINGERPRINT_GROUPS: dict[str, tuple[str, ...]] = {
    "模糊比拟": (
        "仿佛", "犹如", "宛若", "好像", "似乎", "如同",
    ),
    "弱化副词": (
        "微微", "轻轻", "缓缓", "淡淡", "隐隐", "些许", "几分", "一丝", "一抹",
    ),
    "自动心理反应": (
        "不由得", "不禁", "忍不住", "情不自禁", "心中一动", "心头一震", "心下一沉",
    ),
    "解释与总结": (
        "值得注意的是", "不难发现", "由此可见", "总而言之", "这意味着", "这说明",
        "这一切都说明", "他终于明白", "她终于明白", "他意识到", "她意识到",
    ),
    "书面连接": (
        "与此同时", "于是乎", "从而", "因而", "诚然", "然而", "因此", "随之",
        "在此之前", "在此之后", "另一方面",
    ),
    "顺滑推进副词": (
        "随即", "随后", "很快", "立刻", "立即", "逐渐", "慢慢", "忽然", "突然",
        "终于", "始终", "一直", "反复", "来回", "片刻", "几秒后",
    ),
    "神态动作套件": (
        "深吸一口气", "眼中闪过", "嘴角勾起", "眉头微皱", "瞳孔微缩", "脸色一变",
        "目光坚定", "不容置疑", "若有所思", "余光", "视线", "指腹", "愣住",
        "冷笑了一声", "屏住呼吸", "呼吸顿了一下",
    ),
    "空泛宏大词": (
        "命运", "宿命", "注定", "崭新的一页", "一切都变了", "意义深远",
        "前所未有", "无法言喻", "难以形容",
    ),
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=……)")
_LEADING_PUNCTUATION_RE = re.compile(r"^[\s\"'“”‘’《》【】（）()，、；：—…]+")
_TEXT_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_SYMMETRIC_TEMPLATE_RES = (
    re.compile(r"不是[^。！？\n]{0,36}(?:而是|却是)"),
    re.compile(r"与其[^。！？\n]{0,36}不如"),
    re.compile(r"一边[^。！？\n]{0,28}一边[^。！？\n]{0,28}一边"),
    re.compile(r"有的[^。！？\n]{0,28}有的[^。！？\n]{0,28}有的"),
)
def _visible_length(value: str) -> int:
    return sum(1 for char in value if _TEXT_CHAR_RE.match(char))


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(value) if part.strip()]


def _variation(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return (pstdev(values) / average) if average else 0.0


def analyze_de_ai_fingerprints(original_text: str) -> dict[str, Any]:
    """Return compact, deterministic signals used to target a rewrite.

    The report is intentionally heuristic.  It does not pretend to predict a
    third-party detector score; it simply prevents every chapter from receiving
    the same generic rewrite instructions.
    """

    source = str(original_text or "")
    paragraphs = [line.strip() for line in source.splitlines() if line.strip()]
    sentences = _sentences(source)
    sentence_lengths = [_visible_length(item) for item in sentences if _visible_length(item)]
    paragraph_lengths = [_visible_length(item) for item in paragraphs if _visible_length(item)]

    phrase_groups: list[dict[str, Any]] = []
    for label, phrases in DE_AI_FINGERPRINT_GROUPS.items():
        hits = [(phrase, source.count(phrase)) for phrase in phrases if phrase in source]
        if hits:
            phrase_groups.append({
                "label": label,
                "count": sum(count for _, count in hits),
                "examples": [phrase for phrase, _ in hits[:4]],
            })

    symmetric_count = sum(len(pattern.findall(source)) for pattern in _SYMMETRIC_TEMPLATE_RES)

    opening_counter: Counter[str] = Counter()
    for sentence in sentences:
        clean = _LEADING_PUNCTUATION_RE.sub("", sentence)
        opening = "".join(_TEXT_CHAR_RE.findall(clean))[:2]
        if len(opening) == 2:
            opening_counter[opening] += 1
    repeated_openings = [
        {"opening": opening, "count": count}
        for opening, count in opening_counter.most_common(4)
        if count >= 3
    ]

    comma_count = source.count("，") + source.count(",")
    terminal_count = sum(source.count(mark) for mark in "。！？!?")
    return {
        "character_count": _visible_length(source),
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentence_lengths),
        "average_sentence_length": round(mean(sentence_lengths), 1) if sentence_lengths else 0.0,
        "sentence_length_variation": round(_variation(sentence_lengths), 2),
        "paragraph_length_variation": round(_variation(paragraph_lengths), 2),
        "comma_terminal_ratio": round(comma_count / max(1, terminal_count), 2),
        "phrase_groups": phrase_groups,
        "symmetric_template_count": symmetric_count,
        "repeated_openings": repeated_openings,
    }


def build_de_ai_fidelity_audit_prompt(
    original_text: str,
    candidate_chunks: list[str],
) -> str:
    """Ask a separate model turn to audit story semantics, not prose style."""

    rendered_chunks = "\n\n".join(
        f"【候选片段 {index}】\n{chunk}"
        for index, chunk in enumerate(candidate_chunks, start=1)
    )
    return "\n\n".join([
        "核对下面的小说原文与候选片段。你只做故事保真审计，不评价文风，也不要求照抄原句。",
        "【判定范围】\n"
        "逐项检查人物身份与代词、时间、地点、数量、物件归属、对白说话人和条件、因果、"
        "人物已知信息、事件先后、结尾发现。只有语义相同但措辞不同，不算问题。\n"
        "数量必须同时核对总数与分项关系：原文给出总数 N 时，候选不得先把 N 件写成一组，"
        "随后又用‘连同、另有、其余’追加 M 件而造成总量增加；若 N 本来包含后述分项，"
        "候选措辞也必须让包含关系明确。\n"
        "事件先后不能只核对‘两件事都出现了’：先定位原文中每个会改变人物已知信息、"
        "物件位置或持有人、真假判断、出入口状态的对白与动作，再按出现顺序逐一对照候选。"
        "尤其核对揭示性对白与紧邻的取出、递交、打开、藏入等动作；原文 A 后 B，候选写成"
        "B 后 A，即使 A、B 都保留，也必须以 order 判失败。\n"
        "省去不影响情节的灯光、气味、外貌、视线、神态、走位微动作或同义复述不算遗漏；"
        "对白压缩或改口只要说话人、条件和信息不变也应通过。疑问句只是在询问同一件事时，"
        "‘今晚不急着走吧’与‘今晚急着离开吗’这类正反问法不自动构成条件写反；只有回答分支、"
        "人物选择或已知信息随之改变才算冲突。这个宽容不适用于陈述事实中的‘没有联系’与"
        "‘主动联系’等真正正反变化。\n"
        "原文引号内或作为固定称谓出现的第一人称必须按字面核对，例如‘给我信的人’是一个完整称谓。"
        "候选保留该称谓不等于切换叙事人称，也不等于改变收信人；只有称谓所指、说话人或事件参与者"
        "确实改变时才能报 role。\n"
        "原文若在事件已经展示后集中回顾人物、期限、线索、物件流转，或旁白总结‘这些线索仍无法解释某事’，"
        "候选只要在全章前文已呈现对应事件与悬念，就可以删掉这段复盘；不得把省略重复复盘判为 missing。"
        "物件最后一次出现及其交接已写清、且正文没有擅自给出确定去向时，也不要求再用旁白声明‘去向不明’。\n"
        "下列任一情况必须判失败：遗漏承载情节的事实；把条件正反（这里指陈述条件而非逻辑等价问法）、"
        "真假、主动被动、人物归属或"
        "事件顺序写反；新增会改变读者对故事理解的动作、解释、动机、结论或设定。",
        "【输出 JSON】\n"
        '{"passed":true,"issues":[]}\n'
        "或\n"
        '{"passed":false,"issues":[{"chunk":1,"kind":"contradiction",'
        '"detail":"用一句具体中文说明与原文冲突之处"}]}\n'
        "chunk 必须填写 1 到候选片段总数；若问题跨片段，填写最直接造成问题的片段。"
        "kind 只能是 missing、contradiction、added、role、order。只输出一个 JSON 对象，"
        "不要 Markdown、解释或修订正文。",
        f"【原文】\n{original_text}",
        rendered_chunks,
    ])


def build_de_ai_style_audit_prompt(candidate_chunks: list[str]) -> str:
    """Audit structural machine-writing signals without rewriting prose."""

    rendered_chunks = "\n\n".join(
        f"【候选片段 {index}】\n{chunk}"
        for index, chunk in enumerate(candidate_chunks, start=1)
    )
    return "\n\n".join([
        "检查下面连续小说片段是否仍有明显的成品化机器叙事结构。只做表达结构审计，"
        "不核对故事事实，不重写正文。",
        "【必须判失败的高置信问题】\n"
        "- recap：场面已经展示后，旁白又把人物、线索、条件或意义成组复盘，并替读者下结论。\n"
        "- checklist：把进门、上楼、检查、移动等常规步骤逐项列完，像执行日志。\n"
        "- preamble：用日期、钟点、倒计时、地点和陈设连续定位，像先填写场景坐标再开始叙事。\n"
        "- staged：把悬念按静默、灯灭、声响、观察工具、发现、报告、解释的完整镜头链铺平，"
        "每一步都有过渡和说明，像已经排好的分镜脚本；也包括把事实账本逐条翻成一句或一段，"
        "即使没有使用‘随后’等连接词，读起来仍是一拍一拍验账。\n"
        "- camera：连续依靠‘随后、渐渐、一点点、目光、声音、呼吸、沉默’等镜头标签平滑推进。\n"
        "- exposition：开头或转场集中盘点时间、地点、外貌、灯光、气味、陈设，事件迟迟不动。\n"
        "- uniform：多个段落反复使用同一套环境—动作—对白—解释闭合结构。\n"
        "- stock：关键处用空泛心理、意义判断或漂亮收束代替人物眼前的动作与对白。",
        "【不要误报】\n"
        "必要的时间、编号、路线、物件状态、因果条件和事件顺序不是机器味；单个普通副词也不是问题。"
        "只有整段结构明显符合上面一类时才报告，最多报告三个最影响正文的片段。",
        "【输出 JSON】\n"
        '{"passed":true,"issues":[]}\n'
        "或\n"
        '{"passed":false,"issues":[{"chunk":1,"kind":"recap",'
        '"detail":"用一句具体中文指出该片段哪里在复盘或讲解"}]}\n'
        "chunk 必须为 1 到候选片段总数；kind 只能是 recap、checklist、preamble、staged、"
        "camera、exposition、uniform、stock。只输出一个 JSON 对象，不要 Markdown、解释或修订正文。",
        rendered_chunks,
    ])


def build_anti_ai_system_prompt() -> str:
    """Build the full de-AI writing guidelines for inclusion in system prompts."""
    tier1_flat = []
    for _cat, words in TIER1_BANNED_WORDS.items():
        tier1_flat.extend(words)
    tier1_str = "、".join(tier1_flat)

    return "\n\n".join([
        "【去AI味写作规范 — 必须严格遵守】",
        f"一级禁用词（出现即替换）：{tier1_str}",
        "",
        ALL_AI_PATTERNS,
        "",
        DE_AI_3_PASS_METHOD,
        "",
        STACKED_WRITING_RULE,
        "",
        "【章末总结体检测 — 禁止以下结尾方式】\n"
        "- 总结性感悟\n- 升华式感叹\n- 哲理式收尾\n- 伏笔式预告\n"
        "正确做法：章尾用动作、对话或悬念收束，让情节本身制造余韵。",
        "",
        EMOTION_REPLACEMENT_TABLE,
        "",
        SCENE_REWRITE_EXAMPLES,
        "",
        QUICK_SELF_CHECK,
    ])


def build_de_ai_edit_prompt(source: str) -> str:
    """Ask for bounded local edits while the source remains the authority."""
    report = analyze_de_ai_fingerprints(source)
    phrases = list(dict.fromkeys(
        phrase
        for group in report["phrase_groups"]
        for phrase in group["examples"]
    ))
    focus = "、".join(phrases[:12]) if phrases else "重复解释、整齐句式和空泛判断"
    return "\n\n".join([
        "局部修订下面的小说正文，减少机械套话和重复解释。只改确有问题的句子；"
        "保留每段的事件、动作、对白、信息、顺序、视角和篇幅。不要概括、续写或整章重写。",
        f"本轮可优先检查：{focus}。这些词在具体语境中合理时可以保留。",
        "返回至多 12 处修订，每处 old 是原文中连续、唯一、完全一致的 1～2 句，"
        "new 是同一位置的修订句。不得删除段落或对白，不得用概述替换场面；"
        "每处 new 至少保留 old 的八成篇幅。未列出的文字原样保留。",
        '只输出合法 JSON 对象：{"edits":[{"old":"原文片段","new":"修订片段"}]}。'
        "不要解释或 Markdown。",
        f"【原文】\n{source}",
    ])
