"""Gateway-only safe connection retry with no duplicated native transaction."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError

from app.ai.base import BaseAdapter
from app.ai.transport_timeouts import model_connection_error
from app.core.exceptions import LLMConnectionError, LLMTimeoutError
from app.modules.model_runtime.infrastructure.connection_recovery import call_with_connection_recovery, stream_with_connection_recovery
from app.modules.model_runtime.infrastructure.gateway import LLMGateway
from app.services.creation_agent_errors import safe_creation_agent_error
from app.services.workspace.assistant_public_errors import public_model_failure, public_model_error_message


class ConnectionAdapter(BaseAdapter):
    calls = 0
    fail_count = 0

    @property
    def provider_name(self):
        return "audit"

    def begin(self):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_count:
            raise LLMConnectionError("connect",request_not_sent=True)

    async def chat_completion(self, **kwargs):
        self.begin()
        return {"content":"OK"}

    async def stream_chat_completion(self, **kwargs):
        self.begin()
        yield "OK"
        self.last_stream_finish_reason="stop"

    async def stream_chat_completion_with_tools(self, **kwargs):
        self.begin()
        yield {"type":"tool_call_delta", "index":0, "id":"call-one", "name":"read", "arguments_delta":"{}"}
        yield {"type":"done", "finish_reason":"tool_calls"}


@pytest.fixture
def model(monkeypatch):
    config=SimpleNamespace(api_key="fixture",base_url="https://example.invalid/v1",api_protocol="chat_completions",provider="audit")
    monkeypatch.setattr(LLMGateway,"_parse_model",lambda _: ("audit","fixture"))
    monkeypatch.setattr(LLMGateway,"_load_config",lambda _:config)
    monkeypatch.setattr(LLMGateway,"_get_adapter",lambda _:ConnectionAdapter)
    ConnectionAdapter.calls=0


async def gateway_request(kind, retry):
    args=dict(messages=[{"role":"user","content":"fixture"}],model="audit:fixture",timeout=3,retry=retry)
    if kind=="complete":return await LLMGateway.chat_completion(**args)
    if kind=="text":return [x async for x in LLMGateway.stream_chat_completion(**args,resume=0)]
    return [x async for x in LLMGateway.stream_chat_completion_with_tools(**args,resume=0,tools=[
        {"type":"function","function":{"name":"read","parameters":{"type":"object"}}}])]


@pytest.mark.parametrize("kind",["complete","text","tools"])
def test_one_failed_connection_recovers_with_general_retry_disabled(model,kind):
    ConnectionAdapter.fail_count=1
    with patch("asyncio.sleep",new=AsyncMock()):result=asyncio.run(gateway_request(kind,0))
    assert ConnectionAdapter.calls==2
    if kind=="complete":assert result["content"]=="OK"
    elif kind=="text":assert result==["OK"]
    else:assert len([x for x in result if x["type"]=="tool_call_delta"])==1


@pytest.mark.parametrize("kind",["complete","text","tools"])
def test_connection_retry_exhaustion_never_stacks_with_general_retries(model,kind):
    ConnectionAdapter.fail_count=100
    with patch("asyncio.sleep",new=AsyncMock()),pytest.raises(LLMConnectionError) as caught:
        asyncio.run(gateway_request(kind,3))
    assert ConnectionAdapter.calls==2
    assert caught.value.connection_recovery_exhausted is True


@pytest.mark.parametrize("phase",["read","write","response"])
def test_uncertain_delivery_is_not_retried_by_connection_recovery(phase):
    call=AsyncMock(side_effect=LLMConnectionError(phase))
    with pytest.raises(LLMConnectionError):asyncio.run(call_with_connection_recovery(call))
    assert call.await_count==1


def test_no_stream_replay_after_any_provider_event():
    calls=[]
    async def source():
        calls.append(1)
        yield {"type":"reasoning_delta","delta":"started"}
        raise LLMConnectionError("connect",request_not_sent=True)
    async def run():
        received=[]
        with pytest.raises(LLMConnectionError):
            async for event in stream_with_connection_recovery(source):received.append(event)
        assert len(received)==1
    asyncio.run(run())
    assert len(calls)==1


def test_cancellation_during_connection_backoff_does_not_send_again():
    call=AsyncMock(side_effect=LLMTimeoutError("connect",20,request_not_sent=True))
    async def run():
        task=asyncio.create_task(call_with_connection_recovery(call))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())
    assert call.await_count==1


def test_actual_sdk_connection_error_is_public_network_failure_not_unknown():
    from app.modules.operations.domain.failures import classify_failure
    assert classify_failure("OpenAI 连接错误: Connection error.")=="network"
    request=httpx.Request("POST","https://example.invalid/v1/responses")
    error=APIConnectionError(request=request)
    error.__cause__=httpx.ConnectError("private TLS detail",request=request)
    normalized=model_connection_error(error)
    failure=public_model_failure(normalized)
    message,details=safe_creation_agent_error(normalized)
    assert normalized.phase=="connect"
    assert failure.failure_class==details["failure_class"]=="network"
    assert failure.message==message==public_model_error_message(failure.code)
    assert "private" not in message and "处理失败" not in message


@pytest.mark.parametrize("boundary",["proxy_connect","origin_headers","redirect"])
def test_real_http_dispatch_distinguishes_proxy_from_origin_delivery(boundary):
    from app.ai.openai_adapter import OpenAIAdapter, TracedOpenAIHttpClient
    from openai import AsyncOpenAI
    calls=[]
    async def handler(request):
        calls.append(request)
        if boundary=="redirect" and len(calls)==1:
            return httpx.Response(307,headers={"location":"https://redirect.invalid/v1/chat/completions"})
        if boundary!="proxy_connect" or len(calls)==1:
            if boundary!="redirect":
                method=b"CONNECT" if boundary=="proxy_connect" else b"POST"
                await request.extensions["trace"]("http11.send_request_headers.started",{"request":SimpleNamespace(method=method)})
            raise httpx.ConnectError("synthetic connection boundary",request=request)
        return httpx.Response(200,json={"id":"fixture","object":"chat.completion","created":0,"model":"fixture",
            "choices":[{"index":0,"message":{"role":"assistant","content":"OK"},"finish_reason":"stop"}]})
    async def run():
        client=TracedOpenAIHttpClient(transport=httpx.MockTransport(handler))
        sdk=AsyncOpenAI(api_key="fixture",base_url="https://example.invalid/v1",http_client=client,max_retries=0)
        adapter=OpenAIAdapter(api_key="fixture",base_url="https://example.invalid/v1",api_protocol="chat_completions")
        adapter._get_client=lambda:sdk
        async def send():return await adapter.chat_completion([{"role":"user","content":"fixture"}],"fixture")
        try:
            if boundary=="proxy_connect":
                with patch("asyncio.sleep",new=AsyncMock()):result=await call_with_connection_recovery(send)
                assert result["content"]=="OK" and len(calls)==2
            else:
                with pytest.raises(LLMConnectionError) as caught:await call_with_connection_recovery(send)
                assert not caught.value.request_not_sent
                assert len(calls)==(2 if boundary=="redirect" else 1)
        finally:await sdk.close()
    asyncio.run(run())
