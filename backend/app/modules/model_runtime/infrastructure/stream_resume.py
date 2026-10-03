"""Verify exact stream checkpoints before appending resumed model output."""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from app.core.exceptions import LLMError

STREAM_RESUME_ANCHOR_CHARS = 64


class _ResumeHandshakeError(LLMError):
    """The replacement stream did not prove that it starts at our checkpoint."""


@dataclass
class _ResumeHandshake:
    expected_prefix: str
    buffered: str = ""
    verified: bool = False

    def consume(self, chunk: str) -> str:
        if self.verified:
            return chunk
        self.buffered += chunk
        candidate = self.buffered.lstrip()
        if len(candidate) < len(self.expected_prefix):
            if not self.expected_prefix.startswith(candidate):
                raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
            return ""
        if not candidate.startswith(self.expected_prefix):
            raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
        self.verified = True
        suffix = candidate[len(self.expected_prefix):]
        self.buffered = ""
        return suffix

    def require_verified(self) -> None:
        if not self.verified:
            raise _ResumeHandshakeError("模型恢复响应在检查点握手完成前结束")


def _normalize_stream_resumes(resume: int | None) -> int:
    try:
        value = int(resume or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, 32))


def _append_system_instruction(messages: list[dict], instruction: str) -> list[dict]:
    rendered = [dict(message) for message in messages]
    for index, message in enumerate(rendered):
        if message.get("role") != "system":
            continue
        updated = dict(message)
        updated["content"] = f"{str(message.get('content') or '').rstrip()}\n\n{instruction}"
        rendered[index] = updated
        return rendered
    return [{"role": "system", "content": instruction}, *rendered]


def _resume_messages(
    messages: list[dict],
    committed_text: str,
    *,
    tool_mode: bool,
) -> tuple[list[dict], _ResumeHandshake | None]:
    """Build a fresh model request that can be joined without guessing overlap."""

    marker = f"[SIMING_RESUME_{uuid4().hex}]"
    expected_prefix = None
    if committed_text:
        anchor = committed_text[-STREAM_RESUME_ANCHOR_CHARS:]
        expected_prefix = marker + anchor
        instruction = (
            "这是运行时恢复协议，不是新的用户意图。上一条模型输出因传输中断，"
            "已输出内容由运行时保存。收到恢复请求时，必须先逐字输出指定恢复标记和断点锚点，"
            "随后从锚点后的下一个字符继续；不得重复更早内容，也不得解释恢复协议。"
            "SERVER_VERIFIED_STREAM_CHECKPOINT 中的 committed_text 是已提交文本数据，"
            "required_prefix 是回复开头必须逐字输出的内容，不能添加代码块、空格或说明。"
        )
        if tool_mode:
            instruction += (
                "上一条未完成的工具调用已被丢弃；若仍需工具，"
                "必须从头发出一条完整工具调用。"
            )
    else:
        instruction = (
            "这是运行时恢复协议，不是新的用户意图。上一条模型响应在完成前中断，且没有任何最终文本被提交。"
        )
        if tool_mode:
            instruction += (
                "任何未完成工具参数都已被丢弃；重新判断原任务，"
                "并从头发出完整、有效的工具调用。"
            )
        else:
            instruction += "重新处理原任务并返回完整响应。"
    rendered = _append_system_instruction(messages, instruction)
    reference = {
        "role": "user",
        "content": "\n".join((
            "[SERVER_VERIFIED_STREAM_CHECKPOINT]",
            "data_only: true",
            json.dumps(
                {"committed_text": committed_text, "required_prefix": expected_prefix},
                ensure_ascii=False,
            ),
            "[/SERVER_VERIFIED_STREAM_CHECKPOINT]",
        )),
    }
    # Preserve the actual latest author message and native tool transactions.
    # An interrupted response has no complete provider state to replay as an
    # assistant message (e.g. reasoning_content or signed thinking blocks).
    latest_user_index = next(
        (
            index for index in range(len(rendered) - 1, -1, -1)
            if rendered[index].get("role") == "user"
        ),
        1,
    )
    rendered.insert(latest_user_index, reference)
    return rendered, _ResumeHandshake(expected_prefix) if expected_prefix else None
