"""Unified LLM gateway with provider-safe request shaping and retries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TypeVar

from app.ai.anthropic_adapter import AnthropicAdapter
from app.ai.base import BaseAdapter
from app.ai.capabilities import (
    normalize_retry_count,
    provider_capabilities,
    request_meta,
    sanitize_tool_request,
    should_retry_without_tool_choice,
)
from app.ai.deepseek_adapter import DeepSeekAdapter
from app.ai.gemini_adapter import GeminiAdapter
from app.ai.local_runtime_adapter import LocalRuntimeAdapter
from app.ai.openai_adapter import OpenAIAdapter
from app.ai.qwen_adapter import QwenAdapter
from app.core.exceptions import LLMError, NotFoundError
from app.modules.context.interfaces.runtime import active_context_manifest
from app.modules.model_runtime.application.runtime import get_model_runtime
from app.modules.model_runtime.domain.configuration import TaskModelSelection
from app.modules.model_runtime.domain.policy import (
    local_runtime_disabled,
    local_runtime_disabled_message,
)

from .local_cli import (
    LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    LocalCLIAdapter,
    detect_cli_quota_error,
    effective_local_cli_model,
    is_local_cli_provider,
)

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "deepseek": DeepSeekAdapter,
    "qwen": QwenAdapter,
    "gemini": GeminiAdapter,
    "claude_cli": LocalCLIAdapter,
    "codex_cli": LocalCLIAdapter,
    "opencode_cli": LocalCLIAdapter,
    "mimocode_cli": LocalCLIAdapter,
    "cursor_cli": LocalCLIAdapter,
    "kilocode_cli": LocalCLIAdapter,
    "qwen_code_cli": LocalCLIAdapter,
    "hermes_cli": LocalCLIAdapter,
    "openclaw_cli": LocalCLIAdapter,
    "custom_cli": LocalCLIAdapter,
    "local_llama_cpp": LocalRuntimeAdapter,
}

DEFAULT_TIMEOUT = 120
MAX_RETRIES = 3
T = TypeVar("T")


def record_gateway_failure(provider: str, error: BaseException | object) -> None:
    get_model_runtime().record_failure(provider, error)


def _is_auth_error(error: BaseException) -> bool:
    text = str(error)
    return "API Key 无效" in text or "Authentication" in text or "401" in text


def _is_non_retryable(error: BaseException) -> bool:
    text = str(error)
    if "InvalidToken" in text or "登录凭据无效" in text:
        return True
    return (
        _is_auth_error(error)
        or bool(detect_cli_quota_error(text))
        or "未找到" in text
        or "不支持的模型提供商" in text
    )


def _apply_active_context_manifest(
    messages: list[dict],
    extra_body: dict | None,
    max_tokens: int | None,
) -> tuple[list[dict], dict | None, int | None]:
    """Inject the executor-selected manifest for every governed gateway call."""
    active = active_context_manifest()
    if active is None:
        return messages, extra_body, max_tokens

    body = dict(extra_body or {})
    body.setdefault("moshu_context_manifest_id", active.manifest_id)
    output_limit = active.output_reserve_tokens
    if output_limit > 0:
        max_tokens = min(max_tokens, output_limit) if max_tokens else output_limit
    # A handler that already deliberately renders its selected categories (the
    # chapter writer) marks the body to prevent duplicate prompt material.
    if body.get("moshu_context_manifest_rendered") or not active.rendered_context:
        return messages, body, max_tokens
    context_message = {
        "role": "system",
        "content": "Use only this governed task context as project evidence.\n\n"
        + active.rendered_context,
    }
    if messages and messages[0].get("role") == "system":
        rendered_messages = [messages[0], context_message, *messages[1:]]
    else:
        rendered_messages = [context_message, *messages]
    body["moshu_context_manifest_rendered"] = True
    return rendered_messages, body, max_tokens


class LLMGateway:
    """Single entry point for all LLM calls.

    The gateway owns cross-provider behavior: resolving configured models,
    stripping incompatible request parameters, applying timeouts, and retrying
    transient failures. Adapters only translate one request to one provider.
    """

    @staticmethod
    def _parse_model(model: str | None) -> tuple[str, str]:
        return get_model_runtime().parse_model(model)

    @staticmethod
    def _get_global_default_model() -> tuple[str, str]:
        return get_model_runtime().parse_model(None)

    @staticmethod
    def _resolve_provider_by_model(model_name: str) -> tuple[str, str]:
        return get_model_runtime().resolve_provider(model_name)

    @staticmethod
    def _get_adapter(provider: str) -> type[BaseAdapter]:
        adapter_cls = ADAPTER_MAP.get(provider)
        if adapter_cls:
            return adapter_cls
        # Unknown providers are user-defined OpenAI-compatible endpoints. The
        # config layer requires a custom base URL before they can be saved.
        return OpenAIAdapter

    @staticmethod
    def _model_for_task(model: str | None, extra_body: dict | None) -> str | None:
        if not extra_body:
            return model
        task_type = str(extra_body.get("moshu_task_type") or "").strip()
        if not task_type:
            return model
        selection = LLMGateway.select_model_for_task(
            task_type=task_type,
            model_override=model,
            extra_body=extra_body,
        )
        return selection.model

    @staticmethod
    def _identity_from_model_value(model: str | None) -> tuple[str | None, str | None, str | None]:
        if not model:
            return None, None, None
        try:
            provider, model_name = LLMGateway._parse_model(model)
            return f"{provider}:{model_name}", provider, model_name
        except Exception:
            provider, sep, model_name = model.partition(":")
            if sep and provider and model_name:
                return model, provider, model_name
            return model, None, model

    @classmethod
    def select_model_for_task(
        cls,
        *,
        task_type: str,
        model_override: str | None = None,
        extra_body: dict | None = None,
        prefer_task_model: bool = False,
    ) -> TaskModelSelection:
        """Resolve a task model without letting local task settings hide globals.

        Priority: explicit model > explicit task-local opt-in > global default >
        task-local fallback. The fallback keeps old offline-only installs usable,
        while configured API/CLI defaults remain the normal path.
        """
        return get_model_runtime().select_for_task(
            task_type=task_type,
            model_override=model_override,
            extra_body=extra_body,
            prefer_task_model=prefer_task_model,
        )

    @classmethod
    def provider_for_model(cls, model: str | None = None) -> str:
        provider, _ = cls._parse_model(model)
        return provider

    @classmethod
    def model_identity(
        cls, model: str | None = None, extra_body: dict | None = None
    ) -> tuple[str, str]:
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if is_local_cli_provider(provider):
            model_name = effective_local_cli_model(provider, model_name)
        return provider, model_name

    @classmethod
    def supports_tool_calling(cls, model: str | None = None) -> bool:
        provider = cls.provider_for_model(model)
        caps = provider_capabilities(provider)
        return caps.supports_tools and caps.supports_streaming_tools

    @classmethod
    def local_cli_extra_body(
        cls,
        model: str | None = None,
        *,
        cwd: str | None = None,
        attachments: list[str] | None = None,
        base: dict | None = None,
    ) -> dict | None:
        """Attach local filesystem runtime context only for local CLI models."""
        try:
            provider = cls.provider_for_model(model)
        except NotFoundError:
            return base
        if not is_local_cli_provider(provider):
            return base
        payload = dict(base or {})
        if cwd:
            payload["local_cli_cwd"] = cwd
        if attachments:
            payload["local_cli_attachments"] = attachments
        return payload

    @staticmethod
    def _load_config(provider: str):
        return get_model_runtime().provider_config(provider)

    @staticmethod
    async def _call_with_retry(
        *,
        attempts: int,
        timeout_seconds: int | None,
        call_factory: Callable[[], Awaitable[T]],
    ) -> T:
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                if timeout_seconds is None:
                    return await call_factory()
                return await asyncio.wait_for(call_factory(), timeout=timeout_seconds)
            except TimeoutError:
                last_error = LLMError(f"请求超时（{timeout_seconds}秒）")
            except LLMError as exc:
                last_error = exc
                if _is_non_retryable(exc):
                    raise
            except Exception as exc:  # provider SDKs occasionally raise transport errors directly
                last_error = LLMError(f"调用失败: {exc}")

            if attempt < attempts:
                await asyncio.sleep(min(8, attempt * 1.5))

        raise last_error or LLMError("请求失败，已达到最大重试次数")

    @staticmethod
    def _local_cli_timeout_body(
        adapter_cls: type[BaseAdapter],
        extra_body: dict | None,
        timeout_seconds: int | None,
    ) -> tuple[dict | None, int | None]:
        if adapter_cls is not LocalCLIAdapter:
            return extra_body, timeout_seconds
        body = dict(extra_body or {})
        body.setdefault("local_cli_timeout_seconds", timeout_seconds)
        try:
            from app.modules.operations.interfaces.runtime import current_operation_id

            operation_id = current_operation_id()
            if operation_id:
                body.setdefault("operation_id", operation_id)
        except Exception:
            pass
        raw_grace_seconds = body.pop("local_cli_timeout_grace_seconds", None)
        try:
            grace_seconds = int(raw_grace_seconds)
        except (TypeError, ValueError):
            grace_seconds = LOCAL_CLI_TIMEOUT_GRACE_SECONDS
        grace_seconds = max(0, min(grace_seconds, LOCAL_CLI_TIMEOUT_GRACE_SECONDS))
        return body, timeout_seconds + grace_seconds if timeout_seconds is not None else None

    @staticmethod
    def _timeout_value(timeout: int | None) -> int | None:
        if timeout == 0:
            return None
        return timeout or DEFAULT_TIMEOUT

    @staticmethod
    async def _next_stream_item(
        generator: AsyncGenerator[T, None], timeout_seconds: int | None
    ) -> T:
        if timeout_seconds is None:
            return await generator.__anext__()
        return await asyncio.wait_for(generator.__anext__(), timeout=timeout_seconds)

    @classmethod
    async def chat_completion(
        cls,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: int | None = None,
        retry: int = MAX_RETRIES,
        extra_body: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> dict:
        messages, extra_body, max_tokens = _apply_active_context_manifest(
            messages,
            extra_body,
            max_tokens,
        )
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if local_runtime_disabled(provider):
            raise LLMError(local_runtime_disabled_message())
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=config.api_key,
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
            api_protocol=getattr(config, "api_protocol", None) or "chat_completions",
        )
        timeout_seconds = cls._timeout_value(timeout)
        call_extra_body, wait_timeout_seconds = cls._local_cli_timeout_body(
            adapter_cls,
            extra_body,
            timeout_seconds,
        )
        attempts = normalize_retry_count(retry)
        safe_tools, safe_tool_choice, notes = sanitize_tool_request(provider, tools, tool_choice)

        async def _call() -> dict:
            return await adapter.chat_completion(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=call_extra_body,
                tools=safe_tools,
                tool_choice=safe_tool_choice,
            )

        try:
            result = await cls._call_with_retry(
                attempts=attempts,
                timeout_seconds=wait_timeout_seconds,
                call_factory=_call,
            )
        except LLMError as exc:
            if safe_tool_choice is not None and should_retry_without_tool_choice(exc):
                notes.append("接口拒绝 tool_choice，已自动去掉该参数重试")
                try:
                    result = await cls._call_with_retry(
                        attempts=1,
                        timeout_seconds=wait_timeout_seconds,
                        call_factory=lambda: adapter.chat_completion(
                            messages=messages,
                            model=model_name,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            extra_body=call_extra_body,
                            tools=safe_tools,
                            tool_choice=None,
                        ),
                    )
                except LLMError as retry_error:
                    record_gateway_failure(provider, retry_error)
                    raise
            else:
                record_gateway_failure(provider, exc)
                raise

        result.setdefault("model", model_name)
        result["request_meta"] = request_meta(provider, model_name, notes)
        return result

    @classmethod
    async def stream_chat_completion(
        cls,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: int | None = None,
        retry: int = MAX_RETRIES,
        extra_body: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        messages, extra_body, max_tokens = _apply_active_context_manifest(
            messages,
            extra_body,
            max_tokens,
        )
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if local_runtime_disabled(provider):
            raise LLMError(local_runtime_disabled_message())
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=config.api_key,
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
            api_protocol=getattr(config, "api_protocol", None) or "chat_completions",
        )
        timeout_seconds = cls._timeout_value(timeout)
        call_extra_body, wait_timeout_seconds = cls._local_cli_timeout_body(
            adapter_cls,
            extra_body,
            timeout_seconds,
        )
        attempts = normalize_retry_count(retry)
        last_error: BaseException | None = None

        for attempt in range(1, attempts + 1):
            produced = False
            try:
                gen = adapter.stream_chat_completion(
                    messages=messages,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=call_extra_body,
                )
                while True:
                    try:
                        chunk = await cls._next_stream_item(gen, wait_timeout_seconds)
                    except StopAsyncIteration:
                        return
                    produced = True
                    yield chunk
            except TimeoutError:
                last_error = LLMError(f"流式请求超时（{timeout_seconds or '未限制'}秒）")
            except LLMError as exc:
                last_error = exc
                if _is_non_retryable(exc) or produced:
                    record_gateway_failure(provider, exc)
                    raise
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                if produced:
                    record_gateway_failure(provider, last_error)
                    raise last_error from exc

            if attempt < attempts:
                await asyncio.sleep(min(8, attempt * 1.5))

        final_error = last_error or LLMError("流式请求失败，已达到最大重试次数")
        record_gateway_failure(provider, final_error)
        raise final_error

    @classmethod
    async def stream_chat_completion_with_tools(
        cls,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: int | None = None,
        retry: int = MAX_RETRIES,
        extra_body: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        messages, extra_body, max_tokens = _apply_active_context_manifest(
            messages,
            extra_body,
            max_tokens,
        )
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if local_runtime_disabled(provider):
            raise LLMError(local_runtime_disabled_message())
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=config.api_key,
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
            api_protocol=getattr(config, "api_protocol", None) or "chat_completions",
        )
        timeout_seconds = cls._timeout_value(timeout)
        call_extra_body, wait_timeout_seconds = cls._local_cli_timeout_body(
            adapter_cls,
            extra_body,
            timeout_seconds,
        )
        attempts = normalize_retry_count(retry)
        safe_tools, safe_tool_choice, notes = sanitize_tool_request(provider, tools, tool_choice)
        last_error: BaseException | None = None

        for attempt in range(1, attempts + 1):
            produced = False
            try:
                gen = adapter.stream_chat_completion_with_tools(
                    messages=messages,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=call_extra_body,
                    tools=safe_tools,
                    tool_choice=safe_tool_choice,
                )
                while True:
                    try:
                        chunk = await cls._next_stream_item(gen, wait_timeout_seconds)
                    except StopAsyncIteration:
                        return
                    produced = True
                    if chunk.get("type") == "done":
                        chunk.setdefault("request_meta", request_meta(provider, model_name, notes))
                    yield chunk
            except TimeoutError:
                last_error = LLMError(f"流式请求超时（{timeout_seconds or '未限制'}秒）")
            except LLMError as exc:
                last_error = exc
                if (
                    safe_tool_choice is not None
                    and should_retry_without_tool_choice(exc)
                    and not produced
                ):
                    notes.append("接口拒绝 tool_choice，已自动去掉该参数重试")
                    safe_tool_choice = None
                elif _is_non_retryable(exc) or produced:
                    record_gateway_failure(provider, exc)
                    raise
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                if produced:
                    record_gateway_failure(provider, last_error)
                    raise last_error from exc

            if attempt < attempts:
                await asyncio.sleep(min(8, attempt * 1.5))

        final_error = last_error or LLMError("流式请求失败，已达到最大重试次数")
        record_gateway_failure(provider, final_error)
        raise final_error
