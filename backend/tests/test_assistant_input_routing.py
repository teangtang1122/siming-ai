from __future__ import annotations

import asyncio
import json

from app.services import assistant_input_routing as routing


def _run(coro):
    return asyncio.run(coro)


def test_small_document_is_routed_with_its_embedded_instruction_in_full(monkeypatch):
    source = "给司命的要求：把下列人物设定整理到作品资料。\n角色：林野。"
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "content": json.dumps(
                {
                    "route": "creation_material",
                    "resolved_instruction": "把人物设定整理到作品资料",
                    "clarification_question": "",
                    "reason": "要求写在 TXT 内",
                    "confidence": 0.97,
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(routing.LLMGateway, "chat_completion", fake_completion)
    monkeypatch.setattr(routing.LLMGateway, "local_cli_extra_body", lambda *_args, **_kwargs: {})

    result = _run(
        routing.classify_assistant_data_input(
            source_name="人物设定.txt",
            source_text=source,
            source_kind="attachment",
            user_instruction="",
        )
    )

    sent_payload = json.loads(captured["messages"][1]["content"])
    assert sent_payload["chat_instruction"] == ""
    assert sent_payload["source"]["content_view"] == source
    assert "recent_history" not in sent_payload["conversation_context"]
    assert result["route"] == "creation_material"
    assert result["source_coverage"]["coverage"] == "full"


def test_large_document_view_covers_the_end_instead_of_only_truncating_the_head():
    source = "开头" + ("中间资料" * 10_000) + "\n给司命：请总结全文。"

    view, coverage = routing.build_document_routing_view(source, char_limit=8_000)

    assert "给司命：请总结全文。" in view
    assert "原文片段 1" in view
    assert coverage["coverage"] == "distributed"
    assert coverage["omitted_chars"] > 0


def test_model_may_continue_clarifying_and_receives_all_previous_answers(monkeypatch):
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "content": json.dumps(
                {
                    "route": "clarify",
                    "resolved_instruction": "",
                    "clarification_question": "你要总结，还是文学点评？",
                    "reason": "分析类型仍不明确",
                    "confidence": 0.58,
                },
                ensure_ascii=False,
            )
        }

    monkeypatch.setattr(routing.LLMGateway, "chat_completion", fake_completion)
    monkeypatch.setattr(routing.LLMGateway, "local_cli_extra_body", lambda *_args, **_kwargs: {})
    exchanges = [
        {"question": "你想分析它，还是写入作品资料？", "answer": "分析一下"},
        {"question": "分析哪个方面？", "answer": "内容层面"},
    ]

    result = _run(
        routing.classify_assistant_data_input(
            source_name="灰港.txt",
            source_text="林野来到灰港。",
            source_kind="attachment",
            user_instruction="",
            clarification_history=exchanges,
        )
    )

    sent_payload = json.loads(captured["messages"][1]["content"])
    assert sent_payload["clarification"]["history"] == exchanges
    assert result["route"] == "clarify"
    assert result["clarification_question"] == "你要总结，还是文学点评？"


def test_router_failure_stays_safe_even_after_an_earlier_question(monkeypatch):
    async def fail_completion(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(routing.LLMGateway, "chat_completion", fail_completion)
    monkeypatch.setattr(routing.LLMGateway, "local_cli_extra_body", lambda *_args, **_kwargs: {})

    result = _run(
        routing.classify_assistant_data_input(
            source_name="灰港.txt",
            source_text="林野来到灰港。",
            source_kind="attachment",
            user_instruction="",
            clarification_history=[{"question": "怎么处理？", "answer": "还没想好"}],
        )
    )

    assert result["route"] == "clarify"
    assert result["classification_status"] == "safe_fallback"
