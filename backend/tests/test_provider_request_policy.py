"""PC and Android run the same endpoint/protocol request cases."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.capabilities import sanitize_tool_request
from app.ai.deepseek_adapter import DeepSeekAdapter
from app.ai.openai_adapter import OpenAIAdapter
from app.ai.request_policy import provider_extra_body

CASES = json.loads((Path(__file__).resolve().parents[2] /
                    "contracts/fixtures/model_request_policy_cases.json").read_text(encoding="utf-8"))
TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_policy_cases(case):
    assert (provider_extra_body(case["extra"], case["url"], case["protocol"],
                                text_only=case["text_only"]) or {}) == case["expected"]
    _, choice, _ = sanitize_tool_request(
        case["label"], TOOLS, "required", base_url=case["url"], api_protocol=case["protocol"],
    )
    assert choice == case["tool_choice"]


@pytest.mark.parametrize("adapter_class", [OpenAIAdapter, DeepSeekAdapter])
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_real_adapter_sends_protocol_specific_defaults(case, adapter_class):
    adapter = adapter_class(api_key="fixture-key", base_url=case["url"], api_protocol=case["protocol"])
    client = MagicMock()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
        model=case["model"], usage=None, output=[], output_text="ok", id="response-1", status="completed",
    )
    client.chat.completions.create = AsyncMock(return_value=response)
    client.responses.create = AsyncMock(return_value=response)
    adapter._get_client = lambda: client
    asyncio.run(adapter.chat_completion(
        messages=[{"role": "user", "content": "fixture"}], model=case["model"],
        extra_body=case["extra"], tools=[] if case["text_only"] else TOOLS, tool_choice="required",
    ))
    method = client.responses.create if case["protocol"] == "responses" else client.chat.completions.create
    wire = method.await_args.kwargs
    assert wire.get("extra_body", {}) == case["expected"]
    assert wire.get("tool_choice") == case["tool_choice"]
    assert not any(key.startswith("moshu_") for key in wire.get("extra_body", {}))


def test_official_openai_output_limit_is_total_completion_budget():
    from app.ai.request_policy import provider_output_kwargs
    assert provider_output_kwargs({"max_tokens": 12000}, "https://api.openai.com/v1") == {"max_completion_tokens": 12000}
    assert provider_output_kwargs({"max_tokens": 12000}, "https://custom.example/v1") == {"max_tokens": 12000}


PARAMETER_CASES = json.loads((Path(__file__).resolve().parents[2] /
    "contracts/fixtures/model_parameter_policy_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", PARAMETER_CASES, ids=lambda case: case["id"])
def test_final_parameter_contract_matches_android(case):
    from copy import deepcopy
    from app.ai.request_policy import provider_request_kwargs
    before = deepcopy(case["payload"])
    if "error" in case:
        with pytest.raises(ValueError, match=case["error"]):
            provider_request_kwargs(case["payload"], case["url"], case["protocol"])
    else:
        assert provider_request_kwargs(case["payload"], case["url"], case["protocol"]) == case["expected"]
    assert before == case["payload"]


def test_extra_body_cannot_restore_incompatible_sampling_or_disable_reasoning():
    from app.ai.request_policy import provider_request_kwargs
    source = {"model": "gpt-6-luna", "temperature": 0.3, "extra_body": {
        "temperature": 0.8, "top_p": 0.9, "reasoning": {"effort": "high"}}}
    wire = provider_request_kwargs(source, "https://api.openai.com/v1", "responses")
    assert "temperature" not in wire
    assert wire["extra_body"] == {"reasoning": {"effort": "high"}}
    assert source["extra_body"]["temperature"] == 0.8


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_openai_real_text_adapter_strips_sampling_before_sdk(protocol):
    adapter = OpenAIAdapter(api_key="fixture", base_url="https://api.openai.com/v1", api_protocol=protocol)
    client = MagicMock()
    result = SimpleNamespace(status="completed", output_text="OK", output=[], id="r", usage=None, model="gpt-6-luna",
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=None))])
    client.responses.create = AsyncMock(return_value=result)
    client.chat.completions.create = AsyncMock(return_value=result)
    adapter._get_client = lambda: client
    asyncio.run(adapter.chat_completion([{"role":"user","content":"test"}], "gpt-6-luna", temperature=0.8))
    wire = (client.responses.create if protocol == "responses" else client.chat.completions.create).await_args.kwargs
    assert "temperature" not in wire
    assert "reasoning" not in wire.get("extra_body", {})


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
@pytest.mark.parametrize("kind", ["text_stream", "tool_stream", "tool_nonstream"])
@pytest.mark.parametrize("effort", ["none", "medium"])
def test_all_openai_business_transports_apply_same_parameter_policy(protocol, kind, effort):
    from app.core.exceptions import LLMError
    adapter = OpenAIAdapter(api_key="fixture", base_url="https://api.openai.com/v1", api_protocol=protocol)
    client = MagicMock()
    final = SimpleNamespace(status="completed", output_text="OK", output=[], id="r", usage=None, model="gpt-6-luna",
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=None))])
    async def events():
        if protocol == "responses":
            yield SimpleNamespace(type="response.output_text.delta", delta="OK")
            yield SimpleNamespace(type="response.completed", response=final)
        else:
            yield SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason="stop",
                delta=SimpleNamespace(content="OK", tool_calls=None))], usage=None)
    method = AsyncMock(return_value=final if kind == "tool_nonstream" else events())
    if protocol == "responses": client.responses.create = method
    else: client.chat.completions.create = method
    adapter._get_client = lambda: client
    extra = {"storage_target": "session_draft", "operation_id": "fixture-operation", "moshu_operation_id": "fixture-operation", "moshu_task_type": "planning",
        "local_cli_retry_attempts": 1, "temperature": 0.8, "top_p": 0.9,
        **({"reasoning": {"effort": effort}} if protocol == "responses" else {"reasoning_effort": effort})}
    async def run():
        args = dict(messages=[{"role":"user","content":"test"}], model="gpt-6-luna", temperature=0.8, extra_body=extra)
        if kind == "tool_nonstream": return await adapter.chat_completion(**args, tools=TOOLS)
        stream = adapter.stream_chat_completion(**args) if kind == "text_stream" else adapter.stream_chat_completion_with_tools(**args, tools=TOOLS)
        return [event async for event in stream]
    if protocol == "chat_completions" and effort != "none" and kind != "text_stream":
        with pytest.raises(LLMError, match="Responses API"): asyncio.run(run())
        assert method.await_count == 0
    else:
        asyncio.run(run())
        wire = method.await_args.kwargs
        assert ("temperature" in wire.get("extra_body", {})) == (effort == "none")
        assert wire["extra_body"].get("reasoning", {}).get("effort", wire["extra_body"].get("reasoning_effort")) == effort
        assert "storage_target" not in wire.get("extra_body", {})
        assert "operation_id" not in wire.get("extra_body", {})
        assert "moshu_operation_id" not in wire.get("extra_body", {})
        assert extra["storage_target"] == "session_draft"


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_probe_length_recovery_has_two_call_limit_and_retains_protocol(protocol):
    from app.modules.model_runtime.infrastructure.verification import _probe_openai
    from app.modules.model_runtime.application.verification import ModelProbeRequest
    from app.core.exceptions import LLMError
    from unittest.mock import patch
    client = MagicMock()
    clipped = SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output_text="", output=[], choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="", reasoning_content="thinking"))])
    client.responses.create = AsyncMock(return_value=clipped)
    client.chat.completions.create = AsyncMock(return_value=clipped)
    client.close = AsyncMock()
    with patch("app.modules.model_runtime.infrastructure.verification._openai_client", return_value=client):
        with pytest.raises(LLMError, match="连接测试输出超限"):
            asyncio.run(_probe_openai(ModelProbeRequest(provider="openai", model="gpt-6-luna", base_url="https://api.openai.com/v1", api_protocol=protocol, max_output_tokens=3000)))
    method = client.responses.create if protocol == "responses" else client.chat.completions.create
    other = client.chat.completions.create if protocol == "responses" else client.responses.create
    field = "max_output_tokens" if protocol == "responses" else "max_completion_tokens"
    assert [call.kwargs[field] for call in method.await_args_list] == [1024, 3000]
    assert other.await_count == 0


@pytest.mark.parametrize("status,code,param", [(400,"unsupported_value","temperature"),(404,"model_not_found",None)])
def test_probe_parameter_or_model_error_does_not_switch_protocol(status, code, param):
    import httpx
    from openai import APIStatusError
    from app.modules.model_runtime.infrastructure.verification import _probe_openai
    from app.modules.model_runtime.application.verification import ModelProbeRequest
    from app.core.exceptions import LLMError
    from unittest.mock import patch
    client = MagicMock()
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    client.responses.create = AsyncMock(side_effect=APIStatusError("invalid request", response=response, body={"error":{"code":code,"param":param}}))
    client.chat.completions.create = AsyncMock()
    client.close = AsyncMock()
    with patch("app.modules.model_runtime.infrastructure.verification._openai_client", return_value=client):
        with pytest.raises(LLMError, match="参数或请求错误"):
            asyncio.run(_probe_openai(ModelProbeRequest(provider="openai", model="gpt-6-luna", base_url="https://api.openai.com/v1")))
    assert client.responses.create.await_count == 1
    assert client.chat.completions.create.await_count == 0


@pytest.mark.parametrize("adapter_name,url", [
    ("openai", "https://proxy.example/v1"), ("deepseek", "https://api.deepseek.com"),
    ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
])
def test_configured_omission_reaches_all_compatible_adapters(adapter_name, url):
    from app.ai.qwen_adapter import QwenAdapter
    from app.ai.gemini_adapter import GeminiAdapter
    classes = {"openai": OpenAIAdapter, "deepseek": DeepSeekAdapter, "qwen": QwenAdapter, "gemini": GeminiAdapter}
    adapter = classes[adapter_name](api_key="fixture", base_url=url)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(model="m", usage=None,
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK", tool_calls=None))]))
    adapter._get_client = lambda: client
    asyncio.run(adapter.chat_completion([{"role":"user","content":"test"}], "m", temperature=0.8,
        extra_body={"temperature":None, "top_p":0.9}))
    wire = client.chat.completions.create.await_args.kwargs
    assert "temperature" not in wire and "temperature" not in wire.get("extra_body", {})
    assert wire["extra_body"]["top_p"] == 0.9


def test_user_parameters_are_model_scoped_validated_and_can_explicitly_clear_defaults():
    from app.ai.request_policy import configured_request_body, validate_model_request_parameters, provider_request_kwargs
    profiles = {"gpt-6-luna": {"temperature":None}, "other": {"temperature":0.8}}
    assert configured_request_body(None, profiles, "other") == {"temperature":0.8}
    assert configured_request_body(None, profiles, "third") is None
    body = configured_request_body(None, profiles, "gpt-6-luna")
    assert "temperature" not in provider_request_kwargs({"model":"gpt-6-luna", "temperature":0.3, "extra_body":body}, "https://api.openai.com/v1", "responses")
    for invalid in ({"m":{"model":"other"}}, {"m":{"max_tokens":1}}, {"m":{"api_key":"secret"}}, {"m":{"temperature":float("nan")}}):
        with pytest.raises(ValueError): validate_model_request_parameters(invalid)
    with pytest.raises(ValueError, match="自定义采样参数"):
        configured_request_body(None, {"gpt-6-luna":{"temperature":0.8}}, "gpt-6-luna", base_url="https://api.openai.com/v1")


def test_model_parameters_persist_separately_from_discovery_and_reopen():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.modules.model_runtime.infrastructure.models import APIConfig
    from app.modules.model_runtime.infrastructure.configuration import SqlAlchemyModelConfiguration
    engine = create_engine("sqlite://")
    APIConfig.__table__.create(engine)
    parameters = {"gpt-6-luna":{"temperature":None}, "other":{"temperature":0.8}}
    with Session(engine) as db:
        db.add(APIConfig(provider="openai", default_model="gpt-6-luna", api_key_encrypted="", readiness_status="ready", model_request_parameters=parameters))
        db.commit()
    with Session(engine) as db:
        row = db.query(APIConfig).one()
        row.available_models_json = [{"id":"newly-discovered"}]
        db.commit()
    with Session(engine) as db:
        row = db.query(APIConfig).one()
        assert row.model_request_parameters == parameters
        assert SqlAlchemyModelConfiguration._snapshot(row).model_request_parameters == parameters
    engine.dispose()


def test_native_claude_applies_documented_sampling_rules_and_explicit_omission():
    from app.ai.anthropic_adapter import AnthropicAdapter
    adapter = AnthropicAdapter(api_key="fixture")
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=SimpleNamespace(model="claude-sonnet-5", usage=None,
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="OK")]))
    adapter._get_client = lambda: client
    asyncio.run(adapter.chat_completion([{"role":"user","content":"test"}], "claude-sonnet-5", temperature=0.8,
        extra_body={"temperature":None, "thinking":{"type":"disabled"}}))
    wire = client.messages.create.await_args.kwargs
    assert "temperature" not in wire
    assert wire["extra_body"] == {"thinking":{"type":"disabled"}}


def test_native_claude_explicit_supported_top_p_replaces_business_temperature():
    from app.ai.request_policy import provider_request_kwargs
    wire = provider_request_kwargs({"model":"claude-sonnet-5", "temperature":0.7, "extra_body":{"top_p":0.99}}, "https://api.anthropic.com", "anthropic")
    assert "temperature" not in wire
    assert wire["extra_body"] == {"top_p":0.99}
