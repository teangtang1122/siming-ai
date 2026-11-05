"""Connection budgets and safe phase reporting across real provider SDKs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

from app.ai.openai_adapter import OpenAIAdapter, TracedOpenAIHttpClient
from app.ai.anthropic_adapter import TracedAnthropicHttpClient
from app.ai.transport_timeouts import model_timeout_error
from app.core.exceptions import LLMTimeoutError
from app.modules.model_runtime.infrastructure.verification import _openai_client
from app.services.creation_agent_errors import safe_creation_agent_error
from app.services.workspace.assistant_public_errors import public_model_failure, public_model_error_message


@pytest.mark.parametrize("factory", [TracedOpenAIHttpClient, TracedAnthropicHttpClient])
def test_connection_budget_is_configurable_without_changing_sdk_response_deadlines(factory):
    with patch("app.ai.transport_timeouts.get_settings", return_value=SimpleNamespace(api_connect_timeout_seconds=37)):
        client = factory()
    assert client.timeout.connect == 37
    assert client.timeout.read == 600
    asyncio.run(client.aclose())


@pytest.mark.parametrize("url,trust_env", [("https://api.openai.com/v1", True), ("http://127.0.0.1:1234/v1", False)])
def test_verification_uses_same_connection_budget_and_has_no_hidden_sdk_retry(url, trust_env):
    client = _openai_client(api_key="fixture", base_url=url)
    assert client._client.timeout.connect == 20
    assert client._client.trust_env is trust_env
    assert client.max_retries == 0
    asyncio.run(client.close())


@pytest.mark.parametrize("phase,kind", [("connect",httpx.ConnectTimeout), ("read",httpx.ReadTimeout),
                                      ("write",httpx.WriteTimeout), ("pool",httpx.PoolTimeout)])
def test_sdk_timeout_cause_keeps_actual_transport_phase(phase, kind):
    request = httpx.Request("POST", "https://example.invalid/v1/responses", extensions={"timeout":{phase:23}})
    error = APITimeoutError(request=request)
    error.__cause__ = kind("private transport detail", request=request)
    normalized = model_timeout_error(error)
    assert isinstance(normalized, LLMTimeoutError)
    assert normalized.phase == phase
    assert normalized.timeout_seconds == 23
    assert "private" not in str(normalized)


def test_connection_error_is_consistent_in_creation_workspace_and_restored_history():
    error = LLMTimeoutError("connect",20)
    public = public_model_failure(error)
    message, creation = safe_creation_agent_error(error)
    assert message == public.message == public_model_error_message(public.code)
    assert public.code == "model_connection_timeout"
    assert public.details["timeout_phase"] == "connect"
    assert creation["details"] == public.details
    assert "代理" in creation["next_action"]
    assert "更快" not in creation["next_action"]
    assert "模型响应超时" not in message


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_real_sdk_connect_timeout_is_not_retried_and_never_delivers_content(protocol):
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["connect"] == 20
        raise httpx.ConnectTimeout("synthetic connection timeout", request=request)
    async def run():
        client=TracedOpenAIHttpClient(transport=httpx.MockTransport(handler))
        adapter=OpenAIAdapter(api_key="fixture",base_url="https://example.invalid/v1",api_protocol=protocol)
        from openai import AsyncOpenAI
        sdk=AsyncOpenAI(api_key="fixture",base_url="https://example.invalid/v1",http_client=client,max_retries=0)
        adapter._get_client=lambda:sdk
        try:
            with pytest.raises(LLMTimeoutError) as caught:
                await adapter.chat_completion([{"role":"user","content":"fixture"}],"fixture",max_tokens=64)
            assert caught.value.phase == "connect"
            assert len(calls)==1
        finally:
            await sdk.close()
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["deepseek", "qwen", "gemini", "anthropic"])
@pytest.mark.parametrize("kind", ["complete", "text_stream", "tools_stream"])
def test_other_provider_exits_keep_connection_phase(provider, kind):
    from importlib import import_module
    from unittest.mock import AsyncMock, MagicMock
    module=import_module("app.ai."+provider+"_adapter")
    cls=getattr(module,{"deepseek":"DeepSeekAdapter","qwen":"QwenAdapter","gemini":"GeminiAdapter","anthropic":"AnthropicAdapter"}[provider])
    request=httpx.Request("POST","https://example.invalid/v1",extensions={"timeout":{"connect":20}})
    error=module.APITimeoutError(request=request)
    error.__cause__=httpx.ConnectTimeout("synthetic",request=request)
    client=MagicMock()
    method=AsyncMock(side_effect=error)
    client.chat.completions.create=method
    client.messages.create=method
    stream_context=MagicMock()
    stream_context.__aenter__=method
    client.messages.stream.return_value=stream_context
    adapter=cls(api_key="fixture",base_url="https://example.invalid/v1")
    adapter._get_client=lambda:client
    async def run():
        args=dict(messages=[{"role":"user","content":"fixture"}],model="fixture",max_tokens=64)
        with pytest.raises(LLMTimeoutError) as caught:
            if kind=="complete":await adapter.chat_completion(**args)
            else:
                stream=adapter.stream_chat_completion(**args) if kind=="text_stream" else adapter.stream_chat_completion_with_tools(**args)
                async for _ in stream:
                    pytest.fail("timeout must not deliver output")
        assert caught.value.phase=="connect"
        assert method.await_count==1
    asyncio.run(run())


def test_api_and_stream_keep_timeout_phase_without_producing_output():
    import json
    from unittest.mock import AsyncMock
    from app.core.exceptions import app_exception_handler
    from app.routers.config import ChatCompletionRequest, chat_completion, chat_completion_stream, LLMGateway
    error=LLMTimeoutError("connect",20)
    payload=ChatCompletionRequest(messages=[{"role":"user","content":"fixture"}])
    async def failed_stream(**kwargs):
        raise error
        yield "unreachable"
    async def run():
        with patch.object(LLMGateway,"chat_completion",new=AsyncMock(side_effect=error)):
            with pytest.raises(LLMTimeoutError) as caught:
                await chat_completion(payload)
        response=await app_exception_handler(None,caught.value)
        body=json.loads(response.body)
        assert body["data"]=={"timeout_phase":"connect","timeout_seconds":20}
        with patch.object(LLMGateway,"stream_chat_completion",new=failed_stream):
            response=await chat_completion_stream(payload)
            events=[event async for event in response.body_iterator]
        frame=json.loads(events[0].removeprefix("data: ").strip())
        assert frame["type"]=="error" and frame["timeout_phase"]=="connect"
        assert len(events)==2 and events[1]=="data: [DONE]\n\n"
    asyncio.run(run())
