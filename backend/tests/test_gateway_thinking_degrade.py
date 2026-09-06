"""Gateway retry once with thinking disabled after a thinking-mode rejection.

DeepSeek V4 thinking mode rejects requests that carry tools while replaying
assistant messages without ``reasoning_content``.  The gateway must retry the
exact same logical step once with thinking disabled instead of failing the
author's turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.ai.base import BaseAdapter
from app.ai.capabilities import (
    is_thinking_rejection,
    provider_thinking_disable_body,
)
from app.core.exceptions import LLMError
from app.modules.model_runtime.infrastructure import gateway as gateway_module
from app.modules.model_runtime.infrastructure.gateway import LLMGateway

_THINKING_REJECTION = (
    "DeepSeek API 错误: "
    '{"error":{"message":"The `reasoning_content` in the thinking mode '
    'must be passed back to the API.","type":"invalid_request_error",'
    '"code":"invalid_request_error"}}'
)
_UNRELATED_REJECTION = (
    "DeepSeek API 错误: "
    '{"error":{"message":"bad request","type":"invalid_request_error"}}'
)


class ThinkingProbeAdapter(BaseAdapter):
    provider = "deepseek"
    behaviors: list[str] = []
    bodies: list[dict | None] = []
    calls = 0

    @property
    def provider_name(self) -> str:
        return type(self).provider

    async def chat_completion(self, **_kwargs):
        raise LLMError("unexpected chat_completion call")

    async def stream_chat_completion(self, **_kwargs):
        raise LLMError("unexpected text stream call")

    async def stream_chat_completion_with_tools(self, **kwargs):
        type(self).calls += 1
        type(self).bodies.append(kwargs.get("extra_body"))
        behavior = type(self).behaviors.pop(0)
        if behavior == "reject_thinking":
            raise LLMError(_THINKING_REJECTION)
        if behavior == "reject_unrelated":
            raise LLMError(_UNRELATED_REJECTION)
        yield {"type": "done", "finish_reason": "stop", "usage": None}


@pytest.fixture(autouse=True)
def isolated_gateway(monkeypatch):
    ThinkingProbeAdapter.behaviors = []
    ThinkingProbeAdapter.bodies = []
    ThinkingProbeAdapter.calls = 0
    config = SimpleNamespace(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
        api_protocol="chat_completions",
        cli_command=None,
        cli_args=None,
    )
    monkeypatch.setattr(
        LLMGateway,
        "_parse_model",
        staticmethod(lambda _model: ("deepseek", "deepseek-v4-flash")),
    )
    monkeypatch.setattr(LLMGateway, "_load_config", staticmethod(lambda _provider: config))
    monkeypatch.setattr(
        LLMGateway,
        "_get_adapter",
        staticmethod(lambda _provider: ThinkingProbeAdapter),
    )
    monkeypatch.setattr(gateway_module.asyncio, "sleep", lambda _seconds: None)


async def _collect_tools() -> tuple[list[dict], LLMError | None]:
    received: list[dict] = []
    try:
        async for item in LLMGateway.stream_chat_completion_with_tools(
            messages=[{"role": "user", "content": "probe"}],
            model="deepseek:deepseek-v4-flash",
            timeout=0,
            retry=0,
            tools=[{
                "type": "function",
                "function": {"name": "read", "parameters": {"type": "object"}},
            }],
        ):
            received.append(item)
    except LLMError as exc:
        return received, exc
    return received, None


def test_capabilities_classify_thinking_rejections() -> None:
    assert is_thinking_rejection(LLMError(_THINKING_REJECTION))
    assert is_thinking_rejection(
        LLMError("{\"message\":\"Thinking mode does not support this tool choice\"}")
    )
    assert not is_thinking_rejection(LLMError(_UNRELATED_REJECTION))
    assert not is_thinking_rejection(LLMError("quota exceeded"))


def test_thinking_disable_body_only_for_known_provider() -> None:
    assert provider_thinking_disable_body("deepseek") == {"thinking": {"type": "disabled"}}
    assert provider_thinking_disable_body("openai") is None
    assert provider_thinking_disable_body("siliconflow") is None


def test_tool_stream_retries_once_with_thinking_disabled() -> None:
    ThinkingProbeAdapter.behaviors = ["reject_thinking", "ok"]

    received, error = asyncio.run(_collect_tools())

    assert error is None
    done = received[-1]
    assert done["type"] == "done"
    assert ThinkingProbeAdapter.calls == 2
    assert ThinkingProbeAdapter.bodies[0] is None
    assert ThinkingProbeAdapter.bodies[1] == {"thinking": {"type": "disabled"}}
    adjustments = done["request_meta"]["adjustments"]
    assert any("关闭思考模式" in note for note in adjustments)


def test_tool_stream_degrades_only_once_then_fails_readably() -> None:
    ThinkingProbeAdapter.behaviors = ["reject_thinking", "reject_thinking"]

    received, error = asyncio.run(_collect_tools())

    assert received == []
    assert isinstance(error, LLMError)
    assert "关闭思考模式重试一次" in str(error)
    assert "请新建对话" in str(error)
    assert ThinkingProbeAdapter.calls == 2


def test_unrelated_rejection_is_not_degraded() -> None:
    ThinkingProbeAdapter.behaviors = ["reject_unrelated"]

    received, error = asyncio.run(_collect_tools())

    assert received == []
    assert isinstance(error, LLMError)
    assert ThinkingProbeAdapter.calls == 1
