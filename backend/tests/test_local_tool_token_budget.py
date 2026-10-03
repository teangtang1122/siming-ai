"""Model-bound complete local request counting keeps other providers conservative."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.conversation_context import (
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    TemplateMeasuredRequestCounter,
    Utf8ByteTokenCounter,
    assemble_context_step,
    resolve_generation_model_binding,
)
from app.services.conversation_context.canonical import canonical_json, canonical_sha256
from app.services.conversation_context.contracts import ConversationRole
from app.services.conversation_context.errors import ConversationContextError
from app.services.local_runtime.manager import LocalRuntimeManager

TOOL = {
    "type": "function",
    "function": {
        "name": "chapter_writer",
        "description": "生成正文草稿",
        "parameters": {"type": "object", "properties": {}},
    },
}


def test_managed_runtime_measures_loaded_model_template_and_authenticates(monkeypatch):
    manager = LocalRuntimeManager()
    manager._model_key = "qwen3.8-27b-q3"
    manager._port = 12345
    manager._api_key = "private-test-key"
    monkeypatch.setattr(manager, "_healthy", lambda: True)
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-test-key"
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/apply-template":
            assert body["chat_template_kwargs"] == {"enable_thinking": True}
            prompt = "system user chapter_writer tool" if "tools" in body else "system user"
            return httpx.Response(200, json={"prompt": prompt})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": list(range(len(body["content"].split())))})
        raise AssertionError(request.url.path)

    original_client = httpx.Client
    monkeypatch.setattr(
        "app.services.local_runtime.token_counter.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle)),
    )
    measured = manager.request_token_counter("qwen3.8-27b-q3", [TOOL])

    assert measured is not None
    assert measured.count_request_sections([], [TOOL]) == (2, 66)
    assert "private-test-key" not in measured.counter_id
    assert "private-test-key" not in repr(measured)
    assert [path for path, _ in requests] == [
        "/apply-template", "/tokenize", "/apply-template", "/tokenize",
        "/apply-template", "/apply-template",
    ]
    assert measured.count_text("chapter_writer tool") == 2
    manager._model_key = "newly-loaded-model"
    with pytest.raises(ConversationContextError) as error:
        measured.count_text("chapter_writer tool")  # Cached counts are model-bound too.
    assert error.value.details["reason"] == "local_tokenizer_binding_changed"
    requests.clear()
    assert manager.request_token_counter("another-model", [TOOL]) is None
    assert requests == []


def test_managed_runtime_falls_back_when_template_cannot_be_verified(monkeypatch):
    manager = LocalRuntimeManager()
    manager._model_key = "another-model"
    manager._port = 12345
    manager._api_key = "private-test-key"
    monkeypatch.setattr(manager, "_healthy", lambda: True)
    original_client = httpx.Client
    monkeypatch.setattr(
        "app.services.local_runtime.token_counter.httpx.Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(404))
        ),
    )
    assert manager.request_token_counter("another-model", [TOOL]) is None


def test_full_template_counts_reasoning_and_call_ids_and_fails_closed_after_binding(monkeypatch):
    manager = LocalRuntimeManager()
    manager._model_key = "qwen3.8-27b-q3"
    manager._port = 12345
    manager._api_key = "private-test-key"
    monkeypatch.setattr(manager, "_healthy", lambda: True)
    unavailable = False
    templates = []

    def handle(request):
        body = json.loads(request.content)
        if unavailable:
            return httpx.Response(503)
        if request.url.path == "/apply-template":
            templates.append(body)
            return httpx.Response(200, json={"prompt": canonical_json(body)})
        return httpx.Response(200, json={"tokens": list(range(len(body["content"])))})

    original_client = httpx.Client
    monkeypatch.setattr(
        "app.services.local_runtime.token_counter.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle)),
    )
    counter = manager.request_token_counter("qwen3.8-27b-q3", [TOOL])
    assert counter is not None
    messages = [
        {"role": "user", "content": "最终审阅"},
        {"role": "assistant", "content": "", "reasoning_content": "逐项核对完整资料。" * 300,
         "tool_calls": [{"id": "actual-call", "type": "function", "function": {
             "name": "chapter_writer", "arguments": "{}",
         }}]},
        {"role": "tool", "tool_call_id": "actual-call", "content": "真实工具结果。"},
    ]
    bare, increment = counter.count_request_sections(messages, [TOOL])
    full_request = templates[-1]
    assert full_request["messages"] == messages
    assert full_request["tools"] == [TOOL]
    assert full_request["chat_template_kwargs"] == {"enable_thinking": True}
    assert bare + increment >= len(canonical_json(full_request))
    assert bare + increment < len(canonical_json(full_request).encode("utf-8"))
    unavailable = True
    with pytest.raises(ConversationContextError) as error:
        counter.count_text("尚未缓存的新内容")
    assert error.value.details["reason"] == "local_tokenizer_unavailable"


class _ProfileResolver:
    def __init__(self, provider: str, model_name: str):
        self.provider = provider
        self.model_name = model_name

    def resolve_model_profile(self, model, task_type):
        return SimpleNamespace(
            provider=self.provider,
            model_name=self.model_name,
            context_window_tokens=64_000,
            max_output_tokens=4_096,
            safety_margin_tokens=512,
            known=True,
        )


def _bind(provider: str, model_name: str, protocol: str = "native"):
    return resolve_generation_model_binding(
        orchestrator=_ProfileResolver(provider, model_name),
        model=f"{provider}:{model_name}",
        task_type="assistant",
        protocol=protocol,
        system_prompt="system",
        current_tools=[TOOL],
    )


def test_local_template_count_enters_budget_and_changes_with_model(monkeypatch):
    calls = []

    def measure(model_key, tools):
        calls.append(model_key)
        assert tools == [TOOL]
        count = 200 if model_key == "model-a" else 300
        return TemplateMeasuredRequestCounter(
            counter_id=f"conservative.llama_cpp_request.v1:{model_key}",
            tool_schema_hash=canonical_sha256(tools),
            text_counter=lambda text: len(text),
            request_counter=lambda messages, schemas: (len(canonical_json(messages)), count),
        )

    manager = SimpleNamespace(request_token_counter=measure)
    monkeypatch.setattr("app.services.local_runtime.get_runtime_manager", lambda: manager)

    binding, counter, margin = _bind("local_llama_cpp", "model-a")
    assert isinstance(counter, TemplateMeasuredRequestCounter)
    step = assemble_context_step(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation",
            revision=1,
            project_id="project",
        ),
        turns=(),
        current_user_message=ConversationMessage(
            message_id="current", sequence_no=1,
            role=ConversationRole.USER, content="写正文",
        ),
        model_binding=binding,
        token_counter=counter,
        system_prompt="system",
        current_tools=[TOOL],
        safety_margin_tokens=margin,
    )
    assert step.budget.tool_schema_tokens == 200
    assert step.budget.system_prompt_tokens == len(b"system")
    assert counter.count_value([TOOL]) == len(canonical_json([TOOL]))
    assert binding.tool_schema_hash == canonical_sha256([TOOL])

    next_binding, next_counter, _ = _bind("local_llama_cpp", "model-b")
    assert next_binding.fingerprint != binding.fingerprint
    assert next_counter.count_request_sections([], [TOOL])[1] == 300
    assert calls == ["model-a", "model-b"]
    with pytest.raises(ValueError, match="schemas changed"):
        counter.count_request_sections([], [{"type": "function", "function": {"name": "other"}}])


def test_non_local_or_unavailable_model_keeps_byte_budget(monkeypatch):
    manager = SimpleNamespace(request_token_counter=lambda *_: None)
    monkeypatch.setattr("app.services.local_runtime.get_runtime_manager", lambda: manager)
    _, local_counter, _ = _bind("local_llama_cpp", "not-running")
    _, cloud_counter, _ = _bind("openai", "remote-model")
    _, direct_counter, _ = _bind("local_llama_cpp", "model-a", "direct_mcp")
    assert isinstance(local_counter, Utf8ByteTokenCounter)
    assert isinstance(cloud_counter, Utf8ByteTokenCounter)
    assert isinstance(direct_counter, Utf8ByteTokenCounter)
    assert local_counter.count_value([TOOL]) == len(canonical_json([TOOL]).encode("utf-8"))
