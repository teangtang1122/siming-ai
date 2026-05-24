"""LLM Gateway — unified interface for multiple LLM providers."""
import asyncio
from typing import AsyncGenerator, Optional

from ..database.models import APIConfig
from ..database.session import SessionLocal
from ..core.crypto import decrypt
from ..core.exceptions import LLMError, NotFoundError

from .base import BaseAdapter
from .openai_adapter import OpenAIAdapter
from .anthropic_adapter import AnthropicAdapter
from .deepseek_adapter import DeepSeekAdapter
from .qwen_adapter import QwenAdapter


# Provider → Adapter class mapping
ADAPTER_MAP = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "deepseek": DeepSeekAdapter,
    "qwen": QwenAdapter,
}

# Default timeout and retry settings
DEFAULT_TIMEOUT = 120  # seconds
MAX_RETRIES = 3


class LLMGateway:
    """Unified gateway for LLM calls with routing, retry, and timeout."""

    @staticmethod
    def _parse_model(model: Optional[str]) -> tuple[str, str]:
        """Parse model identifier into (provider, model_name).
        
        Supports formats:
        - "openai:gpt-4o" → ("openai", "gpt-4o")
        - "gpt-4o" → will try to resolve provider from global default
        """
        if not model:
            return LLMGateway._get_global_default_model()
        if ":" in model:
            provider, model_name = model.split(":", 1)
            return provider, model_name
        # No provider prefix — try to find which provider has this model
        return LLMGateway._resolve_provider_by_model(model)

    @staticmethod
    def _get_global_default_model() -> tuple[str, str]:
        """Query DB for global default provider and model."""
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()
            if not config:
                raise NotFoundError("未配置全局默认模型，请先前往系统设置配置API")
            return config.provider, config.default_model
        finally:
            db.close()

    @staticmethod
    def _resolve_provider_by_model(model_name: str) -> tuple[str, str]:
        """Try to resolve provider by model name from DB configs."""
        db = SessionLocal()
        try:
            configs = db.query(APIConfig).all()
            for cfg in configs:
                if cfg.default_model == model_name:
                    return cfg.provider, cfg.default_model
            # Fallback: if model name contains known provider hints
            if "claude" in model_name.lower():
                return "anthropic", model_name
            if "deepseek" in model_name.lower():
                return "deepseek", model_name
            if "qwen" in model_name.lower() or "qwq" in model_name.lower():
                return "qwen", model_name
            # Default to openai
            return "openai", model_name
        finally:
            db.close()

    @staticmethod
    def _get_adapter(provider: str) -> type[BaseAdapter]:
        """Get adapter class by provider name."""
        adapter_cls = ADAPTER_MAP.get(provider)
        if not adapter_cls:
            raise LLMError(f"不支持的模型提供商: {provider}")
        return adapter_cls

    @staticmethod
    def _load_config(provider: str) -> APIConfig:
        """Load APIConfig from DB and decrypt API key."""
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
            if not config:
                raise NotFoundError(f"未找到提供商 '{provider}' 的API配置，请先前往系统设置配置")
            return config
        finally:
            db.close()

    @classmethod
    async def chat_completion(
        cls,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        retry: int = MAX_RETRIES,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> dict:
        """Unified non-streaming chat completion with timeout and retry.

        Args:
            messages: List of {"role": "system|user|assistant|tool", "content": "..."}.
                      tool messages need "tool_call_id" for the matched call.
            model: Model identifier, e.g. "openai:gpt-4o" or "gpt-4o"
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds (default 120)
            retry: Max retry attempts (default 3)
            tools: OpenAI-format function definitions [{"type":"function","function":{...}}, ...]
            tool_choice: "auto" | "none" | {"type":"function","function":{"name":"x"}}

        Returns:
            {"content": str | None, "model": str, "usage": dict, "tool_calls": list[dict] | None}
        """
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        api_key = decrypt(config.api_key_encrypted)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(api_key=api_key, base_url=config.base_url_override)

        timeout_seconds = timeout or DEFAULT_TIMEOUT
        last_error = None

        for attempt in range(1, retry + 1):
            try:
                result = await asyncio.wait_for(
                    adapter.chat_completion(
                        messages=messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        extra_body=extra_body,
                        tools=tools,
                        tool_choice=tool_choice,
                    ),
                    timeout=timeout_seconds,
                )
                return result
            except asyncio.TimeoutError:
                last_error = LLMError(f"请求超时（{timeout_seconds}秒）")
            except LLMError as e:
                last_error = e
                # Authentication errors should not be retried
                if "API Key 无效" in str(e):
                    raise e
            except Exception as e:
                last_error = LLMError(f"调用失败: {e}")

            if attempt < retry:
                await asyncio.sleep(1 * attempt)  # Linear backoff

        raise last_error or LLMError("请求失败，已达最大重试次数")

    @classmethod
    async def stream_chat_completion(
        cls,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        retry: int = MAX_RETRIES,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Unified streaming chat completion — text-only, no tool calls surfaced.

        Yields text token chunks. For tools support, use stream_chat_completion_with_tools().
        On failure, raises LLMError.
        """
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        api_key = decrypt(config.api_key_encrypted)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(api_key=api_key, base_url=config.base_url_override)

        timeout_seconds = timeout or DEFAULT_TIMEOUT
        last_error = None

        for attempt in range(1, retry + 1):
            try:
                # Wrap the async generator with timeout using an async for loop
                gen = adapter.stream_chat_completion(
                    messages=messages,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                )
                # Use asyncio.wait_for on each iteration
                while True:
                    try:
                        chunk = await asyncio.wait_for(gen.__anext__(), timeout=timeout_seconds)
                        yield chunk
                    except asyncio.TimeoutError:
                        raise LLMError(f"流式请求超时（{timeout_seconds}秒）")
                    except StopAsyncIteration:
                        break
                return
            except asyncio.TimeoutError:
                last_error = LLMError(f"请求超时（{timeout_seconds}秒）")
            except LLMError as e:
                last_error = e
                if "API Key 无效" in str(e):
                    raise e
            except Exception as e:
                last_error = LLMError(f"流式调用失败: {e}")

            if attempt < retry:
                await asyncio.sleep(1 * attempt)

        raise last_error or LLMError("流式请求失败，已达最大重试次数")

    @classmethod
    async def stream_chat_completion_with_tools(
        cls,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        retry: int = MAX_RETRIES,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> AsyncGenerator[dict, None]:
        """Unified streaming chat completion with tool call support.

        Yields structured chunks:
            {"type": "content_delta", "delta": str}
            {"type": "tool_call_delta", "index": int, "id": str, "name": str | None, "arguments_delta": str}
            {"type": "done", "finish_reason": str, "usage": dict | None}

        Tool call arguments arrive as incremental deltas. Accumulate them client-side.
        """
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        api_key = decrypt(config.api_key_encrypted)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(api_key=api_key, base_url=config.base_url_override)

        timeout_seconds = timeout or DEFAULT_TIMEOUT
        last_error = None

        for attempt in range(1, retry + 1):
            try:
                gen = adapter.stream_chat_completion_with_tools(
                    messages=messages,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                while True:
                    try:
                        chunk = await asyncio.wait_for(gen.__anext__(), timeout=timeout_seconds)
                        yield chunk
                    except asyncio.TimeoutError:
                        raise LLMError(f"流式请求超时（{timeout_seconds}秒）")
                    except StopAsyncIteration:
                        break
                return
            except asyncio.TimeoutError:
                last_error = LLMError(f"请求超时（{timeout_seconds}秒）")
            except LLMError as e:
                last_error = e
                if "API Key 无效" in str(e):
                    raise e
            except Exception as e:
                last_error = LLMError(f"流式调用失败: {e}")

            if attempt < retry:
                await asyncio.sleep(1 * attempt)

        raise last_error or LLMError("流式请求失败，已达最大重试次数")
