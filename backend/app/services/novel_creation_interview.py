"""Model-driven interview decisions for conversational novel creation."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from app.ai.gateway import LLMGateway
from app.ai.local_cli_adapter import is_local_cli_provider
from app.core.json_repair import parse_json_object
from app.services.observability.run_events import classify_failure


INTERVIEW_MAX_TURNS = 8
INTERVIEW_API_TIMEOUT_SECONDS = 0
INTERVIEW_CLI_TIMEOUT_SECONDS = 0
INTERVIEW_CLI_TIMEOUT_GRACE_SECONDS = 10


class NovelInterviewError(RuntimeError):
    def __init__(self, message: str, *, failure_class: str, next_action: str):
        super().__init__(message)
        self.failure_class = failure_class
        self.next_action = next_action


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_history(qa_history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in qa_history or []:
        if not isinstance(item, dict):
            continue
        question = _text(item.get("question"))
        answer = _text(item.get("answer"))
        if question and answer:
            normalized.append({"question": question, "answer": answer})
    return normalized


def _question_key(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.lower(), flags=re.UNICODE)


def _planning_provider(model: str | None) -> str:
    try:
        provider, _ = LLMGateway.model_identity(model, {"moshu_task_type": "planning"})
    except Exception:
        provider = (model or "").split(":", 1)[0].lower()
    return provider or ""


def _failure_advice(failure_class: str) -> str:
    return {
        "quota_or_rate_limit": "请切换有额度的模型，或等待额度恢复后发送“继续”。",
        "auth": "请在系统设置中重新登录或填写凭据，测试成功后发送“继续”。",
        "timeout": "本轮动态提问已停止；回答已保留，可切换更快的模型后发送“继续”。",
        "empty_response": "模型没有返回文字；回答已保留，请发送“继续”重试或切换模型。",
        "invalid_response": "模型没有按动态采访格式返回；回答已保留，请发送“继续”重试。",
    }.get(failure_class, "回答已保留，请检查当前模型后发送“继续”重试。")


def make_novel_interview_error(
    message: str,
    failure_class: str | None = None,
) -> NovelInterviewError:
    cleaned = _text(message) or "模型未完成动态提问"
    category = failure_class or classify_failure(cleaned) or "unknown"
    advice = _failure_advice(category)
    return NovelInterviewError(
        f"动态采访失败：{cleaned} {advice}",
        failure_class=category,
        next_action=advice,
    )


def _raise_interview_error(message: str, failure_class: str | None = None) -> None:
    raise make_novel_interview_error(message, failure_class)


def _parse_interview_payload(raw: str) -> dict[str, Any]:
    parsed = parse_json_object(raw)
    if parsed is None:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1] if "\n" in clean else clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        try:
            parsed = json.loads(clean.strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            _raise_interview_error("模型返回的动态采访 JSON 无法解析。", "invalid_response")
    if not isinstance(parsed, dict):
        _raise_interview_error("模型返回的动态采访结果不是 JSON 对象。", "invalid_response")
    return parsed


def _normalize_question(payload: dict[str, Any], history: list[dict[str, str]]) -> dict[str, Any]:
    question_payload = payload.get("question")
    if not isinstance(question_payload, dict):
        questions = payload.get("questions")
        question_payload = questions[0] if isinstance(questions, list) and questions else None
    if not isinstance(question_payload, dict):
        _raise_interview_error("模型决定继续采访，但没有给出有效问题。", "invalid_response")

    question = _text(question_payload.get("question"))
    if not question:
        _raise_interview_error("模型返回了空问题。", "empty_response")
    asked = {_question_key(item["question"]) for item in history}
    if _question_key(question) in asked:
        _raise_interview_error("模型重复了已经回答过的问题。", "invalid_response")

    return {
        "question": question,
        "purpose": _text(question_payload.get("purpose")),
        # The conversational entry deliberately stays free-form. The model can
        # decide what to ask, but cannot turn the author interview into a
        # preset questionnaire by returning choices.
        "options": [],
        "type": "text",
    }


async def decide_next_interview_step(
    *,
    user_brief: str,
    qa_history: list[dict[str, Any]] | None = None,
    genre_label: str = "",
    target_audience: str = "",
    platform: str = "",
    model: str | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Let the selected model ask one contextual question or start generation."""
    history = _normalize_history(qa_history)
    if len(history) >= INTERVIEW_MAX_TURNS:
        return {"action": "generate", "reason": "动态采访已达到安全轮次上限，使用现有回答进入方案生成。"}

    context = {
        "original_brief": _text(user_brief),
        "known_genre_label": _text(genre_label),
        "known_target_audience": _text(target_audience),
        "known_platform": _text(platform),
        "conversation": history,
    }
    latest_answer = history[-1]["answer"] if history else "（尚无回答）"
    system = (
        "你是与小说作者共同立项的资深策划编辑。你正在进行自然对话式采访。\n"
        "禁止调用固定问题清单，禁止按预设维度或固定顺序盘问，也不要重复作者已经说过的内容。\n"
        "请阅读完整上下文，尤其关注作者最新回答，自行判断此刻最值得追问的创作分岔。\n\n"
        "决策规则：\n"
        "1. 只有当一个额外答案会实质改变三套概念方案时，才继续提问。\n"
        "2. 每次最多提出一个问题，措辞要像编辑与作者交谈，并明确承接最新回答。\n"
        "3. 不要为了填满类型、主角、世界观、篇幅等表格而提问；作者没提到的内容可以由后续方案合理创造。\n"
        "4. 这个对话入口只接受自由文本回答：question.type 必须为 text，question.options 必须为 []。不要输出选择题、题材预设或预先写好的答案。\n"
        "5. 信息已经足以产生有明显差异的三案时，立即选择 generate。\n\n"
        "只返回以下两种 JSON 之一：\n"
        '{"action":"ask_more","reason":"为什么此刻要问",'
        '"question":{"question":"一个动态问题","purpose":"答案会怎样改变方案",'
        '"options":[],"type":"text"}}\n'
        '{"action":"generate","reason":"为什么信息已经足够"}\n'
        "不要输出 Markdown，不要解释 JSON 之外的内容。"
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "请根据以下实时立项对话决定下一步。\n"
                f"最新回答：{latest_answer}\n"
                f"完整上下文：{json.dumps(context, ensure_ascii=False)}"
            ),
        },
    ]
    provider = _planning_provider(model)
    is_local_cli = is_local_cli_provider(provider)
    timeout = INTERVIEW_CLI_TIMEOUT_SECONDS if is_local_cli else INTERVIEW_API_TIMEOUT_SECONDS
    call_extra_body = dict(extra_body or {})
    try:
        chunks: list[str] = []
        output_chars = 0
        last_report_at = 0.0
        from app.services.operation_runtime import current_operation_id, record_operation_signal

        operation_id = current_operation_id()
        stream = LLMGateway.stream_chat_completion(
            messages=messages,
            model=model,
            temperature=0.6,
            max_tokens=900,
            timeout=timeout,
            retry=0,
            extra_body=call_extra_body or None,
        )
        async for chunk in stream:
            chunks.append(chunk)
            output_chars += len(chunk)
            now = time.monotonic()
            if operation_id and now - last_report_at >= 2:
                last_report_at = now
                record_operation_signal(
                    operation_id,
                    "output",
                    {"output_chars": output_chars, "phase": "novel_interview"},
                    "正在根据你的回答判断下一步",
                )
    except Exception as exc:
        _raise_interview_error(str(exc))

    raw = _text("".join(chunks))
    if not raw:
        _raise_interview_error("没有收到模型的文字回复。", "empty_response")
    payload = _parse_interview_payload(raw)
    action = _text(payload.get("action")).lower()
    if action == "generate":
        return {"action": "generate", "reason": _text(payload.get("reason")) or "模型判断信息已足够。"}
    if action != "ask_more":
        _raise_interview_error("模型没有返回 ask_more 或 generate 决策。", "invalid_response")
    question = _normalize_question(payload, history)
    return {
        "action": "ask_more",
        "reason": _text(payload.get("reason")),
        "questions": [question],
    }


__all__ = [
    "INTERVIEW_API_TIMEOUT_SECONDS",
    "INTERVIEW_CLI_TIMEOUT_GRACE_SECONDS",
    "INTERVIEW_CLI_TIMEOUT_SECONDS",
    "INTERVIEW_MAX_TURNS",
    "NovelInterviewError",
    "decide_next_interview_step",
    "make_novel_interview_error",
]
