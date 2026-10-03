"""OpenAI-compatible adapter backed by Siming's managed llama.cpp runtime."""
from __future__ import annotations

from typing import AsyncGenerator, Optional

from ..services.local_runtime import get_runtime_manager
from ..services.local_runtime.chat_template import local_chat_template_kwargs
from .openai_adapter import OpenAIAdapter


def is_local_runtime_provider(provider: str | None) -> bool:
    return provider == "local_llama_cpp"


class LocalRuntimeAdapter(OpenAIAdapter):
    """Start the required local model before delegating to OpenAI semantics."""

    @property
    def provider_name(self) -> str:
        return "local_llama_cpp"

    def _runtime_context(
        self, model: str, extra_body: dict | None, *, tools_enabled: bool = False,
    ) -> tuple[str, dict]:
        payload = dict(extra_body or {})
        task_type = str(payload.pop("moshu_task_type", "assistant"))
        project_id = payload.pop("moshu_project_id", None)
        context_length = payload.pop("moshu_context_length", None)
        adapter_ids = payload.pop("moshu_adapter_ids", None)
        manager = get_runtime_manager()
        base_url = manager.ensure_running(
            model,
            task_type=task_type,
            project_id=project_id,
            context_length=context_length,
            adapter_ids=adapter_ids,
        )
        runtime_api_key = manager.api_key
        if not runtime_api_key:
            raise RuntimeError("本地模型运行时鉴权未就绪，请停止后重新启动")
        self.api_key = runtime_api_key
        self.base_url = base_url
        # Qwen3.8 Q3 selected the wrong category without reasoning in the
        # local qualification run; its tool rounds need the model's reasoning.
        payload.setdefault(
            "chat_template_kwargs",
            local_chat_template_kwargs(model, tools_enabled=tools_enabled),
        )
        return base_url, payload

    @staticmethod
    def _prefer_json(messages: list[dict], payload: dict) -> dict:
        if "response_format" in payload:
            return payload
        system_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        ).lower()
        if "json" in system_text and any(
            marker in system_text
            for marker in ("合法json", "valid json", "json对象", "json object", "以 { 开头")
        ):
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> dict:
        _, payload = self._runtime_context(model, extra_body, tools_enabled=bool(tools))
        payload = self._prefer_json(messages, payload)
        return await super().chat_completion(
            messages,
            model,
            temperature,
            max_tokens,
            payload,
            tools,
            tool_choice,
        )

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        _, payload = self._runtime_context(model, extra_body)
        payload = self._prefer_json(messages, payload)
        async for chunk in super().stream_chat_completion(
            messages,
            model,
            temperature,
            max_tokens,
            payload,
        ):
            yield chunk

    async def stream_chat_completion_with_tools(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> AsyncGenerator[dict, None]:
        _, payload = self._runtime_context(model, extra_body, tools_enabled=bool(tools))
        payload = self._prefer_json(messages, payload)
        async for chunk in super().stream_chat_completion_with_tools(
            messages,
            model,
            temperature,
            max_tokens,
            payload,
            tools,
            tool_choice,
        ):
            yield chunk
