"""Regression tests for retry boundaries after partial stream output."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.base import BaseAdapter
from app.core.exceptions import LLMError, LLMIncompleteResponseError, LLMOutputLimitError
from app.modules.model_runtime.infrastructure import gateway as gateway_module
from app.modules.model_runtime.infrastructure.gateway import LLMGateway


def _checkpoint(messages):
    reference = next(item for item in messages if item["content"].startswith("[SERVER_VERIFIED_STREAM_CHECKPOINT]"))
    assert reference["role"] == "user"
    return json.loads(reference["content"].splitlines()[2])


class TimeoutBoundaryAdapter(BaseAdapter):
    text_behaviors: list[str] = []
    tool_behaviors: list[str] = []
    text_messages: list[list[dict]] = []
    tool_messages: list[list[dict]] = []
    tool_extra_bodies: list[dict | None] = []
    text_calls = 0
    tool_calls = 0

    @property
    def provider_name(self) -> str:
        return "timeout_probe"

    async def chat_completion(self, **_kwargs):
        return {"content": ""}

    async def stream_chat_completion(self, **kwargs):
        type(self).text_calls += 1
        type(self).text_messages.append(kwargs["messages"])
        behavior = type(self).text_behaviors.pop(0)
        if behavior == "timeout_before_output":
            raise TimeoutError("text timed out before output")
        if behavior == "timeout_after_output":
            yield "prefix"
            raise TimeoutError("text timed out after output")
        if behavior == "length_after_output":
            yield "prefix"
            self.last_stream_finish_reason = "length"
            return
        if behavior in {"resume_success", "resume_timeout"}:
            expected = _checkpoint(kwargs["messages"])["required_prefix"]
            yield expected[:17]
            yield expected[17:] + " suffix"
            if behavior == "resume_timeout":
                raise TimeoutError("resumed text timed out")
            return
        yield "complete"

    async def stream_chat_completion_with_tools(self, **kwargs):
        type(self).tool_calls += 1
        type(self).tool_messages.append(kwargs["messages"])
        type(self).tool_extra_bodies.append(kwargs.get("extra_body"))
        behavior = type(self).tool_behaviors.pop(0)
        if behavior == "reasoning_limit":
            yield {"type": "reasoning_delta", "delta": "thinking"}
            yield {"type": "done", "finish_reason": "length", "usage": None}
            return
        if behavior == "timeout_before_output":
            raise TimeoutError("tool stream timed out before output")
        if behavior == "commentary_timeout":
            yield {"type": "content_delta", "delta": "prefix"}
            raise TimeoutError("tool stream timed out after commentary")
        if behavior == "replacement_reply":
            yield {"type": "content_delta", "delta": "unverified replacement"}
            yield {"type": "done", "finish_reason": "stop"}
            return
        if behavior == "text_resume_success":
            expected = _checkpoint(kwargs["messages"])["required_prefix"]
            yield {"type": "content_delta", "delta": expected + " suffix"}
            yield {"type": "done", "finish_reason": "stop"}
            return
        if behavior == "tool_limit":
            yield {"type": "content_delta", "delta": "prefix"}
            yield {
                "type": "tool_call_delta", "index": 0, "id": "call-truncated",
                "name": "write", "arguments_delta": '{"value":',
            }
            yield {"type": "done", "finish_reason": "length"}
            return
        if behavior == "invalid_json_complete":
            yield {"type": "content_delta", "delta": "不完整的提交说明"}
            yield {
                "type": "tool_call_delta", "index": 0, "id": "call-invalid",
                "name": "write", "arguments_delta": '{"value":',
            }
            yield {"type": "done", "finish_reason": "tool_calls"}
            return
        if behavior in {"refusal", "content_filter"}:
            yield {"type": "content_delta", "delta": "unaccepted response"}
            yield {
                "type": "tool_call_delta", "index": 0, "id": "call-blocked",
                "name": "write", "arguments_delta": '{"value":1}',
            }
            yield {"type": "done", "finish_reason": behavior}
            return
        if behavior == "timeout_after_output":
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-1",
                "name": "write",
                "arguments_delta": '{"value":',
            }
            raise TimeoutError("tool stream timed out after output")
        if behavior in {"eof_after_complete_tool", "commentary_complete_tool_timeout"}:
            if behavior == "commentary_complete_tool_timeout":
                yield {"type": "content_delta", "delta": "现在创建林寒的角色档案。\n\n"}
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-unverified",
                "name": "write",
                "arguments_delta": '{"value": 0}',
            }
            if behavior == "commentary_complete_tool_timeout":
                raise TimeoutError("complete JSON without a provider terminal frame")
            return
        if behavior in {"tool_success", "commentary_tool_success"}:
            if behavior == "commentary_tool_success":
                yield {"type": "content_delta", "delta": "现在保存完整角色档案。"}
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-2",
                "name": "write",
                "arguments_delta": '{"value": 1}',
            }
            yield {"type": "done", "finish_reason": "tool_calls", "usage": None}
            return
        yield {"type": "done", "finish_reason": "stop", "usage": None}


@pytest.fixture(autouse=True)
def isolated_gateway(monkeypatch):
    TimeoutBoundaryAdapter.text_behaviors = []
    TimeoutBoundaryAdapter.tool_behaviors = []
    TimeoutBoundaryAdapter.text_messages = []
    TimeoutBoundaryAdapter.tool_messages = []
    TimeoutBoundaryAdapter.tool_extra_bodies = []
    TimeoutBoundaryAdapter.text_calls = 0
    TimeoutBoundaryAdapter.tool_calls = 0
    config = SimpleNamespace(
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        provider="timeout_probe",
        api_protocol="chat_completions",
        cli_command=None,
        cli_args=None,
    )
    monkeypatch.setattr(
        LLMGateway,
        "_parse_model",
        staticmethod(lambda _model: ("timeout_probe", "model")),
    )
    monkeypatch.setattr(
        LLMGateway,
        "_load_config",
        staticmethod(lambda _provider: config),
    )
    monkeypatch.setattr(
        LLMGateway,
        "_get_adapter",
        staticmethod(lambda _provider: TimeoutBoundaryAdapter),
    )
    monkeypatch.setattr(gateway_module.asyncio, "sleep", AsyncMock())
    return config


async def _collect_text(
    *,
    retry: int,
    resume: int = 0,
    on_resume=None,
) -> tuple[list[str], LLMError | None]:
    received: list[str] = []
    try:
        async for item in LLMGateway.stream_chat_completion(
            messages=[{"role": "user", "content": "probe"}],
            model="timeout_probe:model",
            timeout=1,
            retry=retry,
            resume=resume,
            on_resume=on_resume,
        ):
            received.append(item)
    except LLMError as exc:
        return received, exc
    return received, None


async def _collect_tools(
    *, retry: int, resume: int = 0, max_tokens: int | None = None,
    extra_body: dict | None = None, on_resume=None, messages=None, tools_enabled=True,
) -> tuple[list[dict], LLMError | None]:
    received: list[dict] = []
    try:
        async for item in LLMGateway.stream_chat_completion_with_tools(
            messages=messages or [{"role": "user", "content": "probe"}],
            model="timeout_probe:model",
            timeout=1,
            retry=retry,
            resume=resume,
            max_tokens=max_tokens,
            extra_body=extra_body,
            on_resume=on_resume,
            tools=[{
                "type": "function",
                "function": {"name": "write", "parameters": {"type": "object"}},
            }] if tools_enabled else [],
        ):
            received.append(item)
    except LLMError as exc:
        return received, exc
    return received, None


def test_text_stream_does_not_restart_after_output_when_resume_is_disabled():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_after_output"] * 3

    received, error = asyncio.run(_collect_text(retry=2))

    assert received == ["prefix"]
    assert isinstance(error, LLMError)
    assert "流式请求超时" in str(error)
    assert TimeoutBoundaryAdapter.text_calls == 1


def test_tool_stream_does_not_expose_or_restart_partial_arguments_when_resume_is_disabled():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_after_output"] * 3

    received, error = asyncio.run(_collect_tools(retry=2))

    assert received == []
    assert isinstance(error, LLMError)
    assert "流式请求超时" in str(error)
    assert TimeoutBoundaryAdapter.tool_calls == 1


def test_text_stream_still_retries_timeout_before_first_output():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_before_output", "success"]

    received, error = asyncio.run(_collect_text(retry=1))

    assert received == ["complete"]
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2


def test_tool_stream_still_retries_timeout_before_first_output():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_before_output", "success"]

    received, error = asyncio.run(_collect_tools(retry=1))

    assert [item["type"] for item in received] == ["done"]
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2


def test_text_stream_resumes_from_verified_checkpoint_without_duplicate_prefix():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_after_output", "resume_success"]
    on_resume = AsyncMock()

    received, error = asyncio.run(_collect_text(retry=0, resume=2, on_resume=on_resume))

    assert "".join(received) == "prefix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2
    recovered = TimeoutBoundaryAdapter.text_messages[1]
    assert _checkpoint(recovered)["committed_text"] == "prefix"
    assert not any(item["role"] == "assistant" for item in recovered)
    assert recovered[-1] == {"role": "user", "content": "probe"}
    on_resume.assert_awaited_once()
    assert on_resume.await_args.args[0]["checkpoint_chars"] == len("prefix")


def test_text_stream_resumes_when_provider_stops_at_single_call_token_limit():
    TimeoutBoundaryAdapter.text_behaviors = ["length_after_output", "resume_success"]

    received, error = asyncio.run(_collect_text(retry=0, resume=2))

    assert "".join(received) == "prefix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 2


def test_text_stream_stops_after_rejected_resume_instead_of_repeating_eight_times():
    TimeoutBoundaryAdapter.text_behaviors = ["timeout_after_output", "mismatch"] * 5

    received, error = asyncio.run(_collect_text(retry=2, resume=8))

    assert received == ["prefix"]
    assert "没有按检查点恢复协议" in str(error)
    assert TimeoutBoundaryAdapter.text_calls == 2


def test_tool_stream_discards_unaccepted_commentary_and_accepts_complete_new_reply():
    TimeoutBoundaryAdapter.tool_behaviors = ["commentary_timeout", "replacement_reply"]

    received, error = asyncio.run(_collect_tools(retry=2, resume=8))

    assert [item["delta"] for item in received if item["type"] == "content_delta"] == ["unverified replacement"]
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
    assert TimeoutBoundaryAdapter.tool_messages[0] == TimeoutBoundaryAdapter.tool_messages[1]


def test_native_response_retry_keeps_prior_transactions_and_accepts_complete_call_once():
    TimeoutBoundaryAdapter.tool_behaviors = ["commentary_complete_tool_timeout", "commentary_tool_success"]
    prior = [
        {"role": "user", "content": "为当前已保存章节建档"},
        {"role": "assistant", "content": "", "reasoning_content": "之前的真实状态",
         "tool_calls": [{"id": "prior", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "prior", "content": '{"chapter_id":"chapter-1"}'},
    ]
    on_resume = AsyncMock()

    received, error = asyncio.run(_collect_tools(
        retry=1, resume=8, messages=prior, extra_body={"enable_thinking": False}, on_resume=on_resume,
    ))

    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
    assert TimeoutBoundaryAdapter.tool_messages == [prior, prior]
    assert TimeoutBoundaryAdapter.tool_extra_bodies == [{"enable_thinking": False}] * 2
    assert [item["delta"] for item in received if item["type"] == "content_delta"] == ["现在保存完整角色档案。"]
    assert [item["id"] for item in received if item["type"] == "tool_call_delta"] == ["call-2"]
    assert received[-1]["request_meta"]["adjustments"] == ["中断的工具响应已整批重新获取 1 次"]
    on_resume.assert_awaited_once()
    assert on_resume.await_args.args[0]["checkpoint_chars"] == 0
    assert on_resume.await_args.args[0]["tool_mode"] is True


def test_native_response_retry_is_bounded_and_never_commits_partial_commentary():
    TimeoutBoundaryAdapter.tool_behaviors = ["commentary_complete_tool_timeout"] * 4

    received, error = asyncio.run(_collect_tools(retry=0, resume=2))

    assert received == []
    assert isinstance(error, LLMError)
    assert TimeoutBoundaryAdapter.tool_calls == 3


def test_completed_invalid_tool_json_is_not_retried_as_a_transport_interruption():
    TimeoutBoundaryAdapter.tool_behaviors = ["invalid_json_complete"] * 9

    received, error = asyncio.run(_collect_tools(retry=3, resume=8))

    assert received == []
    assert isinstance(error, LLMError)
    assert "工具参数 JSON 无效" in str(error)
    assert TimeoutBoundaryAdapter.tool_calls == 1


@pytest.mark.parametrize("finish_reason", ["refusal", "content_filter"])
def test_native_provider_refusal_never_exposes_or_retries_its_buffered_calls(finish_reason):
    TimeoutBoundaryAdapter.tool_behaviors = [finish_reason] * 9

    received, error = asyncio.run(_collect_tools(retry=3, resume=8))

    assert received == []
    assert isinstance(error, LLMIncompleteResponseError)
    assert TimeoutBoundaryAdapter.tool_calls == 1


def test_tool_free_event_stream_keeps_verified_prose_checkpoint_behavior():
    TimeoutBoundaryAdapter.tool_behaviors = ["commentary_timeout", "text_resume_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=2, tools_enabled=False))

    assert error is None
    assert "".join(item["delta"] for item in received if item["type"] == "content_delta") == "prefix suffix"
    assert _checkpoint(TimeoutBoundaryAdapter.tool_messages[1])["committed_text"] == "prefix"


@pytest.mark.parametrize("behavior", ["reasoning_limit", "tool_limit"])
def test_custom_provider_tool_output_limit_does_not_attempt_checkpoint_recovery(behavior):
    TimeoutBoundaryAdapter.tool_behaviors = [behavior] * 9

    received, error = asyncio.run(_collect_tools(retry=2, resume=8, max_tokens=4096))

    assert isinstance(error, LLMOutputLimitError)
    assert error.max_tokens == 4096
    assert not any(item["type"] == "tool_call_delta" for item in received)
    assert TimeoutBoundaryAdapter.tool_calls == 1


def test_deepseek_reasoning_only_limit_retries_once_without_thinking(monkeypatch, isolated_gateway):
    isolated_gateway.base_url = "https://api.deepseek.com"
    monkeypatch.setattr(
        LLMGateway, "_parse_model",
        staticmethod(lambda _model: ("deepseek", "deepseek-flash")),
    )
    TimeoutBoundaryAdapter.tool_behaviors = ["reasoning_limit", "tool_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=8, max_tokens=4096))

    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
    assert TimeoutBoundaryAdapter.tool_extra_bodies == [
        None, {"thinking": {"type": "disabled"}},
    ]
    assert TimeoutBoundaryAdapter.tool_messages[1] == [{"role": "user", "content": "probe"}]
    assert [item["id"] for item in received if item["type"] == "tool_call_delta"] == ["call-2"]


def test_deepseek_output_limit_stops_after_one_fallback_with_specific_error(monkeypatch, isolated_gateway):
    isolated_gateway.base_url = "https://api.deepseek.com"
    from app.services.workspace.assistant_public_errors import public_model_failure

    monkeypatch.setattr(
        LLMGateway, "_parse_model",
        staticmethod(lambda _model: ("deepseek", "deepseek-flash")),
    )
    TimeoutBoundaryAdapter.tool_behaviors = ["reasoning_limit", "reasoning_limit"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=8, max_tokens=4096))

    assert all(item["type"] == "reasoning_delta" for item in received)
    assert isinstance(error, LLMOutputLimitError)
    assert TimeoutBoundaryAdapter.tool_calls == 2
    public = public_model_failure(error)
    assert public.code == "model_output_limit"
    assert public.details == {
        "failure_class": "output_limit", "retryable": False, "max_tokens": 4096,
    }


def test_deepseek_explicit_thinking_is_not_overridden_on_output_limit(monkeypatch, isolated_gateway):
    isolated_gateway.base_url = "https://api.deepseek.com"
    monkeypatch.setattr(
        LLMGateway, "_parse_model",
        staticmethod(lambda _model: ("deepseek", "deepseek-flash")),
    )
    TimeoutBoundaryAdapter.tool_behaviors = ["reasoning_limit"]

    _received, error = asyncio.run(_collect_tools(
        retry=0, resume=8, max_tokens=4096,
        extra_body={"thinking": {"type": "enabled"}},
    ))

    assert isinstance(error, LLMOutputLimitError)
    assert TimeoutBoundaryAdapter.tool_calls == 1
    assert TimeoutBoundaryAdapter.tool_extra_bodies == [{"thinking": {"type": "enabled"}}]


def test_text_stream_can_resume_more_than_once_without_repeating_committed_text():
    TimeoutBoundaryAdapter.text_behaviors = [
        "timeout_after_output",
        "resume_timeout",
        "resume_success",
    ]

    received, error = asyncio.run(_collect_text(retry=0, resume=2))

    assert "".join(received) == "prefix suffix suffix"
    assert error is None
    assert TimeoutBoundaryAdapter.text_calls == 3
    assert _checkpoint(TimeoutBoundaryAdapter.text_messages[2])["committed_text"] == "prefix suffix"


def test_tool_stream_discards_partial_json_and_replans_one_complete_call():
    TimeoutBoundaryAdapter.tool_behaviors = ["timeout_after_output", "tool_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=2))

    tool_deltas = [item for item in received if item["type"] == "tool_call_delta"]
    assert [item["arguments_delta"] for item in tool_deltas] == ['{"value": 1}']
    assert [item["type"] for item in received] == ["tool_call_delta", "done"]
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
    assert TimeoutBoundaryAdapter.tool_messages == [[{"role": "user", "content": "probe"}]] * 2


def test_tool_stream_requires_terminal_frame_before_exposing_complete_arguments():
    TimeoutBoundaryAdapter.tool_behaviors = ["eof_after_complete_tool", "tool_success"]

    received, error = asyncio.run(_collect_tools(retry=0, resume=2))

    tool_deltas = [item for item in received if item["type"] == "tool_call_delta"]
    assert [item["id"] for item in tool_deltas] == ["call-2"]
    assert [item["arguments_delta"] for item in tool_deltas] == ['{"value": 1}']
    assert error is None
    assert TimeoutBoundaryAdapter.tool_calls == 2
