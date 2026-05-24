"""Prompt for chapter boundary correction during TXT/DOCX import."""
from __future__ import annotations

import json


IMPORT_CORRECTION_SYSTEM = (
    "你是小说导入流程中的章节边界校验助手，专精于在文本中精确定位章节分界。你的工作不是创作，而是校对——用最少的修正让规则预识别结果更准确。\n\n"
    "【边界判断原则】\n"
    "1. 章节标题行通常具有以下特征：独占一行（上下有空行或段落边界）、包含「第X章/回/卷」等序号结构、长度较短（通常不超过30字）。\n"
    "2. 上下文语义验证：如果候选标题出现在句子中间（如「这是第二回合的较量」），或前后文明显表明它不是标题（如它是人物对话的一部分），则不应将其视为章节边界。\n"
    "3. 字符位置精确度：start_char 必须指向标题行的第一个字符，end_char 必须指向该章节内容结束的位置（通常也就是下一章节标题的开始位置或全文末尾）。\n"
    "4. 重叠处理：如果同一位置附近有多个候选边界，选择最合理的一个——优先完整的标题行，其次考虑上下文的最自然断点。\n\n"
    "【不确定情况处理】\n"
    "- 如果你对一个边界是否正确没有把握，保留该边界但将 needs_review 标记为 true，并在 review_reason 中说明原因。\n"
    "- 常见的需要标记审核的情况：包含「回」「章」等字但不是章节标题（如「第二回合，阿远换了打法」——这是战斗描写而非章节标题）。\n\n"
    "【输出格式】\n"
    "只输出JSON数组，不要输出解释文字：\n"
    "[{\"title\":\"章节标题\",\"start_char\":0,\"end_char\":12345,\"preview\":\"前100字\"}]\n"
    "start_char和end_char必须是相对于全文的字符位置索引（从0开始）。如果候选边界已经正确，可以原样返回。"
)


def build_split_correction_messages(text: str, group: dict) -> list[dict]:
    excerpt_start = max(0, group["start_char"] - 400)
    excerpt_end = min(len(text), group["end_char"] + 400)
    excerpt = text[excerpt_start:excerpt_end]
    return [
        {"role": "system", "content": IMPORT_CORRECTION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"文本总长度：{len(text)} 字符\n\n"
                f"当前块编号：{group['block_index']}\n"
                f"当前块全文坐标范围：{excerpt_start}-{excerpt_end}\n"
                f"规则预识别候选：\n{json.dumps(group['candidates'], ensure_ascii=False)}\n\n"
                f"当前块文本：\n{excerpt}\n\n"
                "请校正该块内章节边界并输出JSON数组。"
            ),
        },
    ]
