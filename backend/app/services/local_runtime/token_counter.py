"""Token measurement bound to one managed llama.cpp process and template."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

import httpx

from app.services.conversation_context.budget import TemplateMeasuredRequestCounter
from app.services.conversation_context.canonical import canonical_json, canonical_sha256
from app.services.conversation_context.errors import ConversationContextError, ConversationContextErrorCode

from .chat_template import local_chat_template_kwargs


def make_request_token_counter(
    manager: Any, model_key: str, tools: Sequence[Mapping[str, Any]],
) -> TemplateMeasuredRequestCounter | None:
    """Probe once; after binding, unavailable or changed runtimes fail closed."""
    with manager._lock:
        if (manager._model_key != model_key or not manager._port or
                not manager._api_key or not manager._healthy()):
            return None
        runtime_identity = (manager._model_key, manager._port, manager._api_key)
        url = f"http://127.0.0.1:{manager._port}"
        headers = {"Authorization": f"Bearer {manager._api_key}"}
        template_kwargs = local_chat_template_kwargs(model_key, tools_enabled=bool(tools))

    def require_same_runtime() -> None:
        if (manager._model_key, manager._port, manager._api_key) != runtime_identity:
            raise ConversationContextError(
                ConversationContextErrorCode.CAPACITY_UNKNOWN,
                "本地模型运行实例已变化，不能沿用上一实例的分词计数。请重试。",
                details={"reason": "local_tokenizer_binding_changed"},
            )

    def post(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        with manager._lock:
            require_same_runtime()
            with httpx.Client(timeout=5.0, trust_env=False) as client:
                response = client.post(url + endpoint, headers=headers, json=body)
                response.raise_for_status()
                return response.json()

    @lru_cache(maxsize=256)
    def tokenize(text: str) -> int:
        tokens = post("/tokenize", {"content": text, "add_special": False}).get("tokens")
        if not isinstance(tokens, list) or not all(
            isinstance(token, int) and not isinstance(token, bool) for token in tokens
        ):
            raise ValueError("llama.cpp returned invalid tokens")
        return len(tokens)

    @lru_cache(maxsize=64)
    def render(messages_json: str, tools_json: str) -> str:
        request: dict[str, Any] = {
            "messages": json.loads(messages_json), "chat_template_kwargs": template_kwargs,
        }
        declared_tools = json.loads(tools_json)
        if declared_tools:
            request["tools"] = declared_tools
        prompt = post("/apply-template", request).get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("llama.cpp returned an empty chat template")
        return prompt

    def measured(callback, *args):
        try:
            with manager._lock:
                require_same_runtime()
                return callback(*args)
        except ConversationContextError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError) as exc:
            raise ConversationContextError(
                ConversationContextErrorCode.CAPACITY_UNKNOWN,
                "无法读取已加载本地模型的分词结果，本次请求未发送。请检查本地模型后重试。",
                details={"reason": "local_tokenizer_unavailable"},
            ) from exc

    def request_sections(messages, schemas) -> tuple[int, int]:
        messages_json = canonical_json(list(messages))
        bare_prompt = render(messages_json, "[]")
        bare_count = tokenize(bare_prompt)
        if not schemas:
            return bare_count, 0
        full_prompt = render(messages_json, canonical_json(list(schemas)))
        names = [str(tool.get("function", {}).get("name") or "") for tool in schemas]
        if not all(name and name in full_prompt for name in names):
            raise ValueError("loaded template did not render every offered tool")
        full_count = tokenize(full_prompt)
        # Account for the actual system/tool join; never reuse a synthetic
        # increment for author data or omit reasoning required by the template.
        increment = max(0, full_count - bare_count)
        return bare_count, increment + max(64, math.ceil(increment * 0.05))

    probe = [
        {"role": "system", "content": "Siming request budget probe"},
        {"role": "user", "content": "Continue"},
    ]
    try:
        probe_counts = measured(request_sections, probe, tools)
    except ConversationContextError:
        return None
    fingerprint = hashlib.sha256(canonical_json([
        model_key, runtime_identity[1], render(canonical_json(probe), canonical_json(list(tools))),
        probe_counts,
    ]).encode("utf-8")).hexdigest()[:20]
    return TemplateMeasuredRequestCounter(
        counter_id=f"conservative.llama_cpp_request.v1:{fingerprint}",
        tool_schema_hash=canonical_sha256(list(tools)),
        text_counter=lambda text: measured(tokenize, text),
        request_counter=lambda messages, schemas: measured(request_sections, messages, schemas),
    )
