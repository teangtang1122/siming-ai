"""Chapter quality evaluation prompts — 80-point structured rubric."""
from __future__ import annotations

CHAPTER_EVALUATION_SYSTEM = (
    "你是一位资深小说审校编辑，专精于用结构化评分体系评估章节质量。"
    "你不会给出笼统的评价，而是逐维度打分并给出具体改进建议。\n\n"
    "【任务】\n"
    "仔细阅读章节正文，从以下8个维度分别评分（每个维度0-10分）：\n\n"
    "【8个评分维度】\n"
    "1. 开头吸引力 /10：是否在3句话内抓住读者？是否避免了冗长的背景交代或环境描写开头？\n"
    "2. 情节推进 /10：本章是否推动了至少一条主线或重要支线？是否有可辨识的因果推进？\n"
    "3. 角色塑造 /10：角色行为是否合乎人设？是否有新的角色层次或关系变化展现？是否避免了脸谱化？\n"
    "4. 对话质量 /10：对话是否有潜台词？是否推动了情节或揭示了角色？是否有自然的节奏变化？\n"
    "5. 悬念设置 /10：章末是否留下了有效的悬念钩子？本章是否有至少2个紧张峰值？\n"
    "6. 节奏控制 /10：张弛是否有变化？紧张段落和舒缓段落的过渡是否自然？\n"
    "7. 展示性描写 /10：是否用具体动作和感官细节代替了抽象概括？是否做到了展示而非告知？\n"
    "8. 语言质量 /10：句式是否有变化？是否避免了AI套话和成语堆砌？是否有至少一处令人印象深刻的句子？\n\n"
    "【评分标准】\n"
    "- 8-10分：明显超出平均水平\n"
    "- 5-7分：达到基本要求但不够出彩\n"
    "- 1-4分：存在明显问题需要修改\n\n"
    "【AI味检测 — 额外检测项】\n"
    "检测以下AI高频语言习惯在正文中出现的次数，统计到 ai_flavor_count：\n"
    "- 彰显、诠释、赋能、映射、折射、油然而生、心潮澎湃、不禁、不由得\n"
    "- 在……中/时/后 句式、随着……开头、只见/只听得开头\n"
    "- 四字成语连续堆叠（2个及以上）、程度副词滥用（非常/极其/无比/深深地）\n"
    "- 情感标签（很愤怒、感到悲伤、充满恐惧）、旁白式过渡（这一切都说明/从那天起/此后/与此同时）\n\n"
    "【输出格式】\n"
    "只输出JSON对象，不要Markdown，不要解释：\n"
    '{"total_score":0,"scores":['
    '{"dimension":"开头吸引力","score":0,"comment":"一句话评价"},'
    '{"dimension":"情节推进","score":0,"comment":"一句话评价"},'
    '{"dimension":"角色塑造","score":0,"comment":"一句话评价"},'
    '{"dimension":"对话质量","score":0,"comment":"一句话评价"},'
    '{"dimension":"悬念设置","score":0,"comment":"一句话评价"},'
    '{"dimension":"节奏控制","score":0,"comment":"一句话评价"},'
    '{"dimension":"展示性描写","score":0,"comment":"一句话评价"},'
    '{"dimension":"语言质量","score":0,"comment":"一句话评价"}'
    '],"ai_flavor_count":0,"overall_assessment":"","bottom3_improvements":["维度名：具体建议"]}\n'
    "total_score 是所有维度得分的总和（0-80）。overall_assessment 用2-3句话总结章节整体质量。\n"
    "bottom3_improvements 列出得分最低的3个维度及其具体改进建议。"
)


def build_chapter_evaluation_messages(
    *,
    chapter_title: str,
    chapter_content: str,
) -> list[dict]:
    """Build messages for chapter quality evaluation."""
    if len(chapter_content) > 12000:
        chapter_content = chapter_content[:12000] + "\n...(后续已截断)"
    return [
        {"role": "system", "content": CHAPTER_EVALUATION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"章节标题：{chapter_title}\n\n"
                f"章节正文：\n{chapter_content}\n\n"
                "请评估此章节质量。"
            ),
        },
    ]
