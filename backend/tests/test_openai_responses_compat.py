"""Regression coverage for custom OpenAI-compatible Responses endpoints."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import NotFoundError

from app.ai.openai_adapter import OpenAIAdapter
from app.core.exceptions import LLMError
from app.modules.model_runtime.application.verification import ModelProbeRequest
from app.modules.model_runtime.infrastructure.verification import (
    ProviderModelVerification,
    _list_openai,
    _openai_client,
    _probe_openai,
)
from app.routers.config import list_provider_models
from app.schemas.config import ModelListRequest


def _not_found(path: str) -> NotFoundError:
    response = httpx.Response(404, request=httpx.Request("POST", path))
    return NotFoundError("route not found", response=response, body={"detail": "Not Found"})


class _ReasoningItem:
    type = "reasoning"

    def model_dump(self, *, exclude_none: bool = True):
        return {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "encrypted-state",
            "summary": [],
        }


def test_auto_protocol_probe_uses_real_responses_call_after_chat_404():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=_not_found("https://proxy.example/codex/chat/completions")
    )
    client.responses.create = AsyncMock(
        return_value=SimpleNamespace(output_text="OK", model="gpt-test")
    )
    client.close = AsyncMock()

    with patch(
        "app.modules.model_runtime.infrastructure.verification.AsyncOpenAI",
        return_value=client,
    ):
        result = asyncio.run(_probe_openai(ModelProbeRequest(
            provider="custom_proxy",
            api_key="secret",
            base_url="https://proxy.example/codex",
            model="gpt-test",
            api_protocol="auto",
        )))

    assert result["api_protocol"] == "responses"
    assert result["base_url"] == "https://proxy.example/codex"
    assert result["reply"] == "OK"
    assert client.chat.completions.create.await_count == 1
    assert client.responses.create.await_count == 1


def test_chat_probe_accepts_reasoning_only_response():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=None,
            reasoning_content="正在确认本地模型连接",
        ))],
    ))
    client.close = AsyncMock()

    with patch(
        "app.modules.model_runtime.infrastructure.verification.AsyncOpenAI",
        return_value=client,
    ):
        result = asyncio.run(_probe_openai(ModelProbeRequest(
            provider="custom_proxy",
            api_key="secret",
            base_url="http://127.0.0.1:57783/v1",
            model="qwen3.5-9b-q4",
            api_protocol="chat_completions",
        )))

    assert result["reasoning_only"] is True
    assert result["reply"] == "正在确认本地模型连接"


def test_managed_local_model_verification_bypasses_readiness_gate():
    runtime_result = {
        "content": "",
        "reasoning_content": "本地运行时已经响应",
    }
    with patch(
        "app.modules.model_runtime.infrastructure.verification.LocalRuntimeAdapter.chat_completion",
        new=AsyncMock(return_value=runtime_result),
    ) as completion:
        result = asyncio.run(ProviderModelVerification().verify(ModelProbeRequest(
            provider="local_llama_cpp",
            model="qwen3.5-9b-q4",
        )))

    assert completion.await_count == 1
    assert result["reasoning_only"] is True
    assert result["reply"] == "本地运行时已经响应"


def test_custom_model_catalog_404_allows_manual_model_entry():
    payload = ModelListRequest(
        provider="custom_proxy",
        api_key="secret",
        base_url_override="https://proxy.example/codex",
    )
    verification = MagicMock()
    verification.list_models = AsyncMock(
        side_effect=LLMError("API error: HTTP 404 Not Found at /models")
    )
    with patch("app.routers.config.get_model_verification", return_value=verification):
        response = asyncio.run(list_provider_models(payload))

    assert response.data["models"] == []
    assert response.data["manual_entry_required"] is True
    assert "手动填写" in response.data["warning"]


def test_custom_model_catalog_empty_result_allows_manual_model_entry():
    payload = ModelListRequest(
        provider="custom_proxy",
        api_key="secret",
        base_url_override="https://proxy.example/codex",
    )
    verification = MagicMock()
    verification.list_models = AsyncMock(return_value=[])
    with patch("app.routers.config.get_model_verification", return_value=verification):
        response = asyncio.run(list_provider_models(payload))

    assert response.data["models"] == []
    assert response.data["manual_entry_required"] is True
    assert "空模型列表" in response.data["warning"]


def test_custom_model_catalog_retries_with_v1_suffix():
    first_client = MagicMock()
    first_client.models.list = AsyncMock(
        side_effect=_not_found("https://proxy.example/codex/models")
    )
    first_client.close = AsyncMock()
    second_client = MagicMock()
    second_client.models.list = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(id="gpt-test")])
    )
    second_client.close = AsyncMock()

    with patch(
        "app.modules.model_runtime.infrastructure.verification.AsyncOpenAI",
        side_effect=[first_client, second_client],
    ) as client_factory:
        models = asyncio.run(_list_openai(ModelProbeRequest(
            provider="custom_proxy",
            api_key="secret",
            base_url="https://proxy.example/codex",
        )))

    assert models == [{"id": "gpt-test", "display_name": "gpt-test"}]
    assert client_factory.call_args_list[0].kwargs["base_url"] == "https://proxy.example/codex"
    assert client_factory.call_args_list[1].kwargs["base_url"] == "https://proxy.example/codex/v1"


def test_loopback_model_probe_bypasses_system_proxy():
    client = MagicMock()
    with patch(
        "app.modules.model_runtime.infrastructure.verification.AsyncOpenAI",
        return_value=client,
    ) as client_factory:
        assert _openai_client(api_key="secret", base_url="http://127.0.0.1:1234/v1") is client

    http_client = client_factory.call_args.kwargs["http_client"]
    assert isinstance(http_client, httpx.AsyncClient)
    assert http_client.trust_env is False
    asyncio.run(http_client.aclose())


def test_responses_adapter_converts_tools_and_preserves_reasoning_state():
    client = MagicMock()
    response = SimpleNamespace(
        output_text="",
        model="gpt-test",
        usage=SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14),
        output=[
            _ReasoningItem(),
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                id="fc_1",
                name="read_project",
                arguments='{"project_id":"p1"}',
            ),
        ],
    )
    client.responses.create = AsyncMock(return_value=response)

    messages = [
        {"role": "system", "content": "Use project facts."},
        {"role": "assistant", "content": None, "provider_state": [_ReasoningItem().model_dump()]},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "prior_call",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "prior_call", "content": "done"},
        {"role": "user", "content": "Continue."},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": "read_project",
            "description": "Read project data",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    with patch("app.ai.openai_adapter.AsyncOpenAI", return_value=client):
        result = asyncio.run(OpenAIAdapter(
            api_key="secret",
            base_url="https://proxy.example/codex",
            api_protocol="responses",
        ).chat_completion(
            messages=messages,
            model="gpt-test",
            tools=tools,
            tool_choice="auto",
            extra_body={"moshu_task_type": "project", "custom_option": True},
        ))

    kwargs = client.responses.create.await_args.kwargs
    assert kwargs["tools"][0]["name"] == "read_project"
    assert kwargs["input"][1]["type"] == "reasoning"
    assert any(item.get("type") == "function_call_output" for item in kwargs["input"])
    assert kwargs["extra_body"] == {"custom_option": True}
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert result["tool_calls"][0]["function"]["name"] == "read_project"
    assert result["provider_state"][0]["encrypted_content"] == "encrypted-state"
    assert result["usage"]["total_tokens"] == 14


def test_responses_stream_maps_text_tool_calls_and_done_metadata():
    response = SimpleNamespace(
        status="completed",
        usage=SimpleNamespace(input_tokens=8, output_tokens=3, total_tokens=11),
        output=[_ReasoningItem()],
    )

    async def stream_events():
        yield SimpleNamespace(
            type="response.output_item.added",
            output_index=0,
            item=SimpleNamespace(type="function_call", call_id="call_1", id="fc_1", name="write", arguments=""),
        )
        yield SimpleNamespace(type="response.function_call_arguments.delta", output_index=0, delta='{"value":1}')
        yield SimpleNamespace(type="response.output_text.delta", delta="working")
        yield SimpleNamespace(type="response.completed", response=response)

    client = MagicMock()
    client.responses.create = AsyncMock(return_value=stream_events())

    async def collect():
        adapter = OpenAIAdapter(
            api_key="secret",
            base_url="https://proxy.example/codex",
            api_protocol="responses",
        )
        return [chunk async for chunk in adapter.stream_chat_completion_with_tools(
            messages=[{"role": "user", "content": "work"}],
            model="gpt-test",
            tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )]

    with patch("app.ai.openai_adapter.AsyncOpenAI", return_value=client):
        chunks = asyncio.run(collect())

    assert any(chunk.get("type") == "content_delta" and chunk.get("delta") == "working" for chunk in chunks)
    assert any(chunk.get("type") == "tool_call_delta" and chunk.get("name") == "write" for chunk in chunks)
    assert any(chunk.get("arguments_delta") == '{"value":1}' for chunk in chunks)
    done = next(chunk for chunk in chunks if chunk.get("type") == "done")
    assert done["usage"]["total_tokens"] == 11
    assert done["provider_state"][0]["encrypted_content"] == "encrypted-state"


def test_chat_streams_ignore_empty_choice_chunks_and_keep_usage():
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)

    async def text_events():
        yield SimpleNamespace(choices=[])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))])

    async def tool_events():
        yield SimpleNamespace(choices=[], usage=usage)
        yield SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="done", tool_calls=None),
                finish_reason="stop",
            )],
            usage=None,
        )

    text_client = MagicMock()
    text_client.chat.completions.create = AsyncMock(return_value=text_events())
    tool_client = MagicMock()
    tool_client.chat.completions.create = AsyncMock(return_value=tool_events())

    async def collect_text():
        adapter = OpenAIAdapter(api_key="secret")
        return [chunk async for chunk in adapter.stream_chat_completion(
            messages=[{"role": "user", "content": "work"}],
            model="gpt-test",
        )]

    async def collect_tools():
        adapter = OpenAIAdapter(api_key="secret")
        return [chunk async for chunk in adapter.stream_chat_completion_with_tools(
            messages=[{"role": "user", "content": "work"}],
            model="gpt-test",
        )]

    with patch("app.ai.openai_adapter.AsyncOpenAI", return_value=text_client):
        assert asyncio.run(collect_text()) == ["hello"]
    with patch("app.ai.openai_adapter.AsyncOpenAI", return_value=tool_client):
        chunks = asyncio.run(collect_tools())

    assert chunks[0] == {"type": "content_delta", "delta": "done"}
    assert chunks[-1]["type"] == "done"
    assert chunks[-1]["usage"]["total_tokens"] == 5
