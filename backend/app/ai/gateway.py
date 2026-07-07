"""Unified LLM gateway with provider-safe request shaping and retries."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Optional, TypeVar

from ..core.crypto import decrypt
from ..core.exceptions import LLMError, NotFoundError
from ..database.models import APIConfig, LocalModelTaskSetting
from ..database.session import SessionLocal
from .anthropic_adapter import AnthropicAdapter
from .base import BaseAdapter
from .capabilities import (
    normalize_retry_count,
    provider_capabilities,
    request_meta,
    sanitize_tool_request,
    should_retry_without_tool_choice,
)
from .deepseek_adapter import DeepSeekAdapter
from .gemini_adapter import GeminiAdapter
from .local_cli_adapter import (
    LocalCLIAdapter,
    effective_local_cli_model,
    is_local_cli_provider,
)
from .local_runtime_adapter import LocalRuntimeAdapter
from .openai_adapter import OpenAIAdapter
from .qwen_adapter import QwenAdapter


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


@dataclass(frozen=True)
class TaskModelSelection:
    model: Optional[str]
    source: str
    provider: Optional[str] = None
    model_name: Optional[str] = None


def _is_auth_error(error: BaseException) -> bool:
    text = str(error)
    return "API Key 无效" in text or "Authentication" in text or "401" in text


def _is_non_retryable(error: BaseException) -> bool:
    text = str(error)
    return _is_auth_error(error) or "未找到" in text or "不支持的模型提供商" in text


class LLMGateway:
    """Single entry point for all LLM calls.

    The gateway owns cross-provider behavior: resolving configured models,
    stripping incompatible request parameters, applying timeouts, and retrying
    transient failures. Adapters only translate one request to one provider.
    """

    @staticmethod
    def _parse_model(model: Optional[str]) -> tuple[str, str]:
        if not model:
            return LLMGateway._get_global_default_model()
        if ":" in model:
            provider, model_name = model.split(":", 1)
            return provider, model_name
        return LLMGateway._resolve_provider_by_model(model)

    @staticmethod
    def _get_global_default_model() -> tuple[str, str]:
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
            if not config:
                raise NotFoundError("未配置全局默认模型，请先前往系统设置配置模型")
            return config.provider, config.default_model
        finally:
            db.close()

    @staticmethod
    def _resolve_provider_by_model(model_name: str) -> tuple[str, str]:
        db = SessionLocal()
        try:
            configs = db.query(APIConfig).all()
            for cfg in configs:
                if cfg.default_model == model_name:
                    return cfg.provider, cfg.default_model
            lowered = model_name.lower()
            if "claude" in lowered:
                return "anthropic", model_name
            if "deepseek" in lowered:
                return "deepseek", model_name
            if "qwen" in lowered or "qwq" in lowered:
                return "qwen", model_name
            if "gemini" in lowered:
                return "gemini", model_name
            return "openai", model_name
        finally:
            db.close()

    @staticmethod
    def _get_adapter(provider: str) -> type[BaseAdapter]:
        adapter_cls = ADAPTER_MAP.get(provider)
        if adapter_cls:
            return adapter_cls
        # Unknown providers are user-defined OpenAI-compatible endpoints. The
        # config layer requires a custom base URL before they can be saved.
        return OpenAIAdapter

    @staticmethod
    def _model_for_task(model: Optional[str], extra_body: Optional[dict]) -> Optional[str]:
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
    def _identity_from_model_value(model: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
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

    @staticmethod
    def _task_setting_model(task_type: str) -> tuple[Optional[str], Optional[LocalModelTaskSetting]]:
        db = SessionLocal()
        try:
            setting = db.query(LocalModelTaskSetting).filter(
                LocalModelTaskSetting.task_type == task_type
            ).first()
            if not setting:
                return None, None
            return f"local_llama_cpp:{setting.model_key}", setting
        finally:
            db.close()

    @staticmethod
    def _global_default_model_value() -> Optional[str]:
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
            if not config:
                return None
            return f"{config.provider}:{config.default_model}"
        finally:
            db.close()

    @classmethod
    def select_model_for_task(
        cls,
        *,
        task_type: str,
        model_override: Optional[str] = None,
        extra_body: Optional[dict] = None,
        prefer_task_model: bool = False,
    ) -> TaskModelSelection:
        """Resolve a task model without letting local task settings hide globals.

        Priority: explicit model > explicit task-local opt-in > global default >
        task-local fallback. The fallback keeps old offline-only installs usable,
        while configured API/CLI defaults remain the normal path.
        """
        task_type = str(task_type or "").strip()
        override = str(model_override or "").strip()
        if override:
            model_value, provider, model_name = cls._identity_from_model_value(override)
            if (
                extra_body is not None
                and task_type
                and provider == "local_llama_cpp"
                and not extra_body.get("moshu_context_length")
            ):
                _setting_model, setting = cls._task_setting_model(task_type)
                if setting and setting.context_length and (not model_name or model_name == setting.model_key):
                    extra_body["moshu_context_length"] = setting.context_length
            return TaskModelSelection(
                model=model_value or override,
                source="explicit",
                provider=provider,
                model_name=model_name,
            )

        if extra_body:
            prefer_task_model = prefer_task_model or bool(
                extra_body.get("moshu_prefer_task_model")
                or extra_body.get("moshu_use_task_model")
            )

        setting_model: Optional[str] = None
        setting: Optional[LocalModelTaskSetting] = None
        if task_type and prefer_task_model:
            setting_model, setting = cls._task_setting_model(task_type)

        selected = ""
        source = ""
        if prefer_task_model and setting_model:
            selected = setting_model
            source = "task_setting"
        if not selected:
            selected = cls._global_default_model_value() or ""
            source = "global_default" if selected else ""
        if not selected and task_type and setting_model is None:
            setting_model, setting = cls._task_setting_model(task_type)
        if not selected and setting_model:
            selected = setting_model
            source = "task_setting_fallback"
        if not selected:
            return TaskModelSelection(model=None, source="unconfigured")

        model_value, provider, model_name = cls._identity_from_model_value(selected)
        if (
            extra_body is not None
            and setting
            and setting.context_length
            and provider == "local_llama_cpp"
            and (not model_name or model_name == setting.model_key)
            and not extra_body.get("moshu_context_length")
        ):
            extra_body["moshu_context_length"] = setting.context_length
        return TaskModelSelection(
            model=model_value or selected,
            source=source or "global_default",
            provider=provider,
            model_name=model_name,
        )

    @classmethod
    def provider_for_model(cls, model: Optional[str] = None) -> str:
        provider, _ = cls._parse_model(model)
        return provider

    @classmethod
    def model_identity(cls, model: Optional[str] = None, extra_body: Optional[dict] = None) -> tuple[str, str]:
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if is_local_cli_provider(provider):
            model_name = effective_local_cli_model(provider, model_name)
        return provider, model_name

    @classmethod
    def supports_tool_calling(cls, model: Optional[str] = None) -> bool:
        provider = cls.provider_for_model(model)
        caps = provider_capabilities(provider)
        return caps.supports_tools and caps.supports_streaming_tools

    @classmethod
    def local_cli_extra_body(
        cls,
        model: Optional[str] = None,
        *,
        cwd: str | None = None,
        attachments: list[str] | None = None,
        base: Optional[dict] = None,
    ) -> Optional[dict]:
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
    def _load_config(provider: str) -> APIConfig:
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
            if not config:
                raise NotFoundError(f"未找到提供商 '{provider}' 的 API 配置，请先前往系统设置配置")
            return config
        finally:
            db.close()

    @staticmethod
    async def _call_with_retry(
        *,
        attempts: int,
        timeout_seconds: int,
        call_factory: Callable[[], Awaitable[T]],
    ) -> T:
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(call_factory(), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
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
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=decrypt(config.api_key_encrypted),
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url_override,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
        )
        timeout_seconds = timeout or DEFAULT_TIMEOUT
        attempts = normalize_retry_count(retry)
        safe_tools, safe_tool_choice, notes = sanitize_tool_request(provider, tools, tool_choice)

        async def _call() -> dict:
            return await adapter.chat_completion(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                tools=safe_tools,
                tool_choice=safe_tool_choice,
            )

        try:
            result = await cls._call_with_retry(
                attempts=attempts,
                timeout_seconds=timeout_seconds,
                call_factory=_call,
            )
        except LLMError as exc:
            if safe_tool_choice is not None and should_retry_without_tool_choice(exc):
                notes.append("接口拒绝 tool_choice，已自动去掉该参数重试")
                result = await cls._call_with_retry(
                    attempts=1,
                    timeout_seconds=timeout_seconds,
                    call_factory=lambda: adapter.chat_completion(
                        messages=messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        extra_body=extra_body,
                        tools=safe_tools,
                        tool_choice=None,
                    ),
                )
            else:
                raise

        result.setdefault("model", model_name)
        result["request_meta"] = request_meta(provider, model_name, notes)
        return result

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
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=decrypt(config.api_key_encrypted),
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url_override,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
        )
        timeout_seconds = timeout or DEFAULT_TIMEOUT
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
                    extra_body=extra_body,
                )
                while True:
                    try:
                        chunk = await asyncio.wait_for(gen.__anext__(), timeout=timeout_seconds)
                    except StopAsyncIteration:
                        return
                    produced = True
                    yield chunk
            except asyncio.TimeoutError:
                last_error = LLMError(f"流式请求超时（{timeout_seconds}秒）")
            except LLMError as exc:
                last_error = exc
                if _is_non_retryable(exc) or produced:
                    raise
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                if produced:
                    raise last_error

            if attempt < attempts:
                await asyncio.sleep(min(8, attempt * 1.5))

        raise last_error or LLMError("流式请求失败，已达到最大重试次数")

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
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        config = cls._load_config(provider)
        adapter_cls = cls._get_adapter(provider)
        adapter = adapter_cls(
            api_key=decrypt(config.api_key_encrypted),
            base_url=config.provider if adapter_cls is LocalCLIAdapter else config.base_url_override,
            cli_command=getattr(config, "cli_command", None),
            cli_args=getattr(config, "cli_args", None),
        )
        timeout_seconds = timeout or DEFAULT_TIMEOUT
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
                    extra_body=extra_body,
                    tools=safe_tools,
                    tool_choice=safe_tool_choice,
                )
                while True:
                    try:
                        chunk = await asyncio.wait_for(gen.__anext__(), timeout=timeout_seconds)
                    except StopAsyncIteration:
                        return
                    produced = True
                    if chunk.get("type") == "done":
                        chunk.setdefault("request_meta", request_meta(provider, model_name, notes))
                    yield chunk
            except asyncio.TimeoutError:
                last_error = LLMError(f"流式请求超时（{timeout_seconds}秒）")
            except LLMError as exc:
                last_error = exc
                if safe_tool_choice is not None and should_retry_without_tool_choice(exc) and not produced:
                    notes.append("接口拒绝 tool_choice，已自动去掉该参数重试")
                    safe_tool_choice = None
                elif _is_non_retryable(exc) or produced:
                    raise
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                if produced:
                    raise last_error

            if attempt < attempts:
                await asyncio.sleep(min(8, attempt * 1.5))

        raise last_error or LLMError("流式请求失败，已达到最大重试次数")
