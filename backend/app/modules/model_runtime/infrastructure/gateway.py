"""Unified LLM gateway with provider-safe request shaping and retries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from inspect import isawaitable
from typing import TypeVar
from uuid import uuid4

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
    CLIPermissionRequiredError,
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
    "dsh_cli": LocalCLIAdapter,
    "custom_cli": LocalCLIAdapter,
    "local_llama_cpp": LocalRuntimeAdapter,
}

DEFAULT_TIMEOUT = 120
MAX_RETRIES = 3
DEFAULT_STREAM_RESUMES = 8
STREAM_RESUME_ANCHOR_CHARS = 64
T = TypeVar("T")


class _ResumeHandshakeError(LLMError):
    """The replacement stream did not prove that it starts at our checkpoint."""


@dataclass
class _ResumeHandshake:
    expected_prefix: str
    buffered: str = ""
    verified: bool = False

    def consume(self, chunk: str) -> str:
        if self.verified:
            return chunk
        self.buffered += chunk
        candidate = self.buffered.lstrip()
        if len(candidate) < len(self.expected_prefix):
            if not self.expected_prefix.startswith(candidate):
                raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
            return ""
        if not candidate.startswith(self.expected_prefix):
            raise _ResumeHandshakeError("模型没有按检查点恢复协议继续输出")
        self.verified = True
        suffix = candidate[len(self.expected_prefix):]
        self.buffered = ""
        return suffix

    def require_verified(self) -> None:
        if not self.verified:
            raise _ResumeHandshakeError("模型恢复响应在检查点握手完成前结束")


def _normalize_stream_resumes(resume: int | None) -> int:
    try:
        value = int(resume or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, 32))


def _append_system_instruction(messages: list[dict], instruction: str) -> list[dict]:
    rendered = [dict(message) for message in messages]
    for index, message in enumerate(rendered):
        if message.get("role") != "system":
            continue
        updated = dict(message)
        updated["content"] = f"{str(message.get('content') or '').rstrip()}\n\n{instruction}"
        rendered[index] = updated
        return rendered
    return [{"role": "system", "content": instruction}, *rendered]


def _resume_messages(
    messages: list[dict],
    committed_text: str,
    *,
    tool_mode: bool,
) -> tuple[list[dict], _ResumeHandshake | None]:
    """Build a fresh model request that can be joined without guessing overlap."""

    marker = f"[SIMING_RESUME_{uuid4().hex}]"
    if committed_text:
        anchor = committed_text[-STREAM_RESUME_ANCHOR_CHARS:]
        expected_prefix = marker + anchor
        instruction = (
            "这是运行时恢复协议，不是新的用户意图。上一条 assistant 输出因传输中断，"
            "已输出内容由运行时保存。收到恢复请求时，必须先逐字输出指定恢复标记和断点锚点，"
            "随后从锚点后的下一个字符继续；不得重复更早内容，也不得解释恢复协议。"
        )
        if tool_mode:
            instruction += (
                "上一条未完成的工具调用已被丢弃；若仍需工具，"
                "必须从头发出一条完整工具调用。"
            )
        rendered = _append_system_instruction(messages, instruction)
        rendered.extend([
            {"role": "assistant", "content": committed_text},
            {
                "role": "user",
                "content": (
                    "继续刚才因传输中断的同一响应。回复开头必须严格等于下面一行，"
                    "不能添加代码块、空格或说明；之后紧接尚未输出的内容：\n"
                    + expected_prefix
                ),
            },
        ])
        return rendered, _ResumeHandshake(expected_prefix)

    instruction = (
        "这是运行时恢复协议，不是新的用户意图。上一条模型响应在完成前中断，且没有任何最终文本被提交。"
    )
    if tool_mode:
        instruction += (
            "任何未完成工具参数都已被丢弃；重新判断原任务，"
            "并从头发出完整、有效的工具调用。"
        )
    else:
        instruction += "重新处理原任务并返回完整响应。"
    rendered = _append_system_instruction(messages, instruction)
    rendered.append({"role": "user", "content": "继续中断的同一模型步骤。"})
    return rendered, None


def _tool_delta_events_complete(events: list[dict]) -> bool:
    if not events:
        return True
    calls: dict[int, dict[str, str]] = {}
    for event in events:
        index = int(event.get("index") or 0)
        call = calls.setdefault(index, {"name": "", "arguments": ""})
        if event.get("name"):
            call["name"] = str(event["name"])
        if event.get("arguments_delta"):
            call["arguments"] += str(event["arguments_delta"])
    for call in calls.values():
        if not call["name"]:
            return False
        if not call["arguments"]:
            return False
        try:
            arguments = json.loads(call["arguments"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(arguments, dict):
            return False
    return True


def _validate_tool_stream_completion(
    done_event: dict | None,
    buffered_tool_events: list[dict],
    handshake: _ResumeHandshake | None,
    content_seen: bool,
    usage_totals: dict[str, int],
    has_usage: bool,
) -> bool:
    if handshake and content_seen:
        handshake.require_verified()
    if done_event is None:
        raise _ResumeHandshakeError("模型工具流在正式结束帧到达前停止")
    raw_usage = done_event.get("usage")
    if isinstance(raw_usage, dict):
        has_usage = True
        for key in usage_totals:
            usage_totals[key] += max(0, int(raw_usage.get(key) or 0))
    finish_reason = str(done_event.get("finish_reason") or "").lower()
    if finish_reason in {"length", "max_tokens", "token_limit", "incomplete"}:
        raise _ResumeHandshakeError("模型输出达到单次长度上限，正在从检查点继续")
    if not _tool_delta_events_complete(buffered_tool_events):
        raise _ResumeHandshakeError("工具调用在参数完整前结束")
    if handshake and not content_seen and not buffered_tool_events:
        handshake.require_verified()
    return has_usage


def _record_stream_resume(
    *,
    provider: str,
    resume_attempt: int,
    checkpoint_chars: int,
    tool_mode: bool,
) -> None:
    try:
        from app.modules.operations.interfaces.runtime import current_operation_id
        from app.services.operation_runtime import record_operation_signal

        operation_id = current_operation_id()
        if operation_id:
            record_operation_signal(
                operation_id,
                "stream_resume",
                {
                    "resume_attempt": resume_attempt,
                    "checkpoint_chars": checkpoint_chars,
                    "tool_mode": tool_mode,
                    "provider": provider,
                },
                message=(
                    "模型连接中断，正在从已验证检查点继续"
                    if checkpoint_chars else "模型工具响应中断，正在重新获取完整工具调用"
                ),
            )
    except Exception:
        # Progress projection must never change model execution semantics.
        pass


async def _notify_stream_resume(
    callback: Callable[[dict], Awaitable[None] | None] | None,
    *,
    provider: str,
    resume_attempt: int,
    checkpoint_chars: int,
    tool_mode: bool,
) -> None:
    payload = {
        "resume_attempt": resume_attempt,
        "checkpoint_chars": checkpoint_chars,
        "tool_mode": tool_mode,
        "provider": provider,
    }
    _record_stream_resume(**payload)
    if callback is None:
        return
    try:
        result = callback(dict(payload))
        if isawaitable(result):
            await result
    except Exception:
        # UI/progress projection is advisory; checkpoint recovery is not.
        pass


def _is_auth_error(error: BaseException) -> bool:
    text = str(error)
    return "API Key 无效" in text or "Authentication" in text or "401" in text


def _is_non_retryable(error: BaseException) -> bool:
    if isinstance(error, CLIPermissionRequiredError):
        return True
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
    # Checkpoint generation and other explicitly isolated runtime calls must
    # not inherit a task manifest from the surrounding ContextVar.  In
    # particular, a checkpoint request may execute while a chapter/planning
    # manifest is active; injecting that project evidence would silently turn
    # the isolated history compressor into another business-model request.
    # Keep the marker in ``extra_body`` so provider adapters can strip it with
    # the other internal ``moshu_*`` keys, but do not bind the active manifest,
    # its output limit, or its rendered project context.
    if bool((extra_body or {}).get("moshu_context_manifest_disabled")):
        return messages, extra_body, max_tokens
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
        first_message = dict(messages[0])
        first_message["content"] = (
            f"{str(first_message.get('content') or '').rstrip()}\n\n"
            f"{context_message['content']}"
        )
        rendered_messages = [first_message, *messages[1:]]
    else:
        rendered_messages = [context_message, *messages]
    body["moshu_context_manifest_rendered"] = True
    return rendered_messages, body, max_tokens


class LLMGateway:
    """Single entry point for all LLM calls.

    The gateway owns cross-provider behavior: resolving configured models,
    rejecting unsupported tool requests, stripping compatible optional
    parameters, applying timeouts, and retrying transient failures. Adapters
    only translate one request to one provider.
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
    ) -> TaskModelSelection:
        """Resolve one model by explicit override, task default, then global default."""
        return get_model_runtime().select_for_task(
            task_type=task_type,
            model_override=model_override,
            extra_body=extra_body,
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
        safe_tools, safe_tool_choice, notes = sanitize_tool_request(provider, tools, tool_choice)
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
                except LLMError:
                    raise
            else:
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
        resume: int = DEFAULT_STREAM_RESUMES,
        on_resume: Callable[[dict], Awaitable[None] | None] | None = None,
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
        raw_retries_remaining = attempts - 1
        resumes_remaining = _normalize_stream_resumes(resume)
        resume_attempt = 0
        last_error: BaseException | None = None
        committed_parts: list[str] = []
        request_messages = messages
        handshake: _ResumeHandshake | None = None

        while True:
            raw_produced = False
            error_cause: BaseException | None = None
            non_retryable = False
            gen: AsyncGenerator[str, None] | None = None
            try:
                adapter.last_stream_finish_reason = None
                gen = adapter.stream_chat_completion(
                    messages=request_messages,
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=call_extra_body,
                )
                while True:
                    try:
                        chunk = await cls._next_stream_item(gen, wait_timeout_seconds)
                    except StopAsyncIteration:
                        break
                    raw_produced = True
                    outgoing = handshake.consume(str(chunk)) if handshake else str(chunk)
                    if outgoing:
                        committed_parts.append(outgoing)
                        yield outgoing
                if handshake:
                    handshake.require_verified()
                finish_reason = str(adapter.last_stream_finish_reason or "").lower()
                if finish_reason in {"length", "max_tokens", "token_limit", "incomplete"}:
                    raise _ResumeHandshakeError(
                        "模型输出达到单次长度上限，正在从检查点继续"
                    )
                return
            except TimeoutError as exc:
                last_error = LLMError(f"流式请求超时（{timeout_seconds or '未限制'}秒）")
                error_cause = exc
            except LLMError as exc:
                last_error = exc
                error_cause = exc
                non_retryable = _is_non_retryable(exc)
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                error_cause = exc
            finally:
                if gen is not None:
                    with suppress(Exception):
                        await gen.aclose()

            if non_retryable:
                raise last_error from error_cause
            if not raw_produced and raw_retries_remaining > 0:
                raw_retries_remaining -= 1
                await asyncio.sleep(min(8, (attempts - raw_retries_remaining) * 1.5))
                continue

            committed_text = "".join(committed_parts)
            can_resume = resumes_remaining > 0 and (
                raw_produced or handshake is not None or bool(committed_text)
            )
            if can_resume:
                resumes_remaining -= 1
                resume_attempt += 1
                request_messages, handshake = _resume_messages(
                    messages,
                    committed_text,
                    tool_mode=False,
                )
                await _notify_stream_resume(
                    on_resume,
                    provider=provider,
                    resume_attempt=resume_attempt,
                    checkpoint_chars=len(committed_text),
                    tool_mode=False,
                )
                await asyncio.sleep(min(8, resume_attempt * 1.5))
                continue

            final_error = last_error or LLMError("流式请求失败，已达到最大重试次数")
            if error_cause is not None:
                raise final_error from error_cause
            raise final_error

    @classmethod
    def _prepare_tool_stream(
        cls,
        messages: list[dict],
        model: str | None,
        max_tokens: int | None,
        timeout: int | None,
        retry: int,
        resume: int,
        extra_body: dict | None,
        tools: list[dict] | None,
        tool_choice: str | dict | None,
    ) -> tuple:
        messages, extra_body, max_tokens = _apply_active_context_manifest(
            messages,
            extra_body,
            max_tokens,
        )
        model = cls._model_for_task(model, extra_body)
        provider, model_name = cls._parse_model(model)
        if local_runtime_disabled(provider):
            raise LLMError(local_runtime_disabled_message())
        safe_tools, safe_tool_choice, notes = sanitize_tool_request(
            provider,
            tools,
            tool_choice,
        )
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
        return (
            messages, model_name, provider, adapter, max_tokens, timeout_seconds,
            call_extra_body, wait_timeout_seconds, attempts,
            _normalize_stream_resumes(resume), safe_tools, safe_tool_choice, notes,
        )

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
        resume: int = DEFAULT_STREAM_RESUMES,
        on_resume: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        (messages, model_name, provider, adapter, max_tokens, timeout_seconds,
         call_extra_body, wait_timeout_seconds, attempts, resumes_remaining,
         safe_tools, safe_tool_choice, notes) = cls._prepare_tool_stream(
            messages, model, max_tokens, timeout, retry, resume, extra_body, tools, tool_choice,
        )
        raw_retries_remaining = attempts - 1
        resume_attempt = 0
        last_error: BaseException | None = None
        committed_parts: list[str] = []
        request_messages = messages
        handshake: _ResumeHandshake | None = None
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        has_usage = False

        while True:
            raw_produced = False
            content_seen = False
            buffered_tool_events: list[dict] = []
            done_event: dict | None = None
            error_cause: BaseException | None = None
            non_retryable = False
            gen: AsyncGenerator[dict, None] | None = None
            try:
                gen = adapter.stream_chat_completion_with_tools(
                    messages=request_messages,
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
                        break
                    raw_produced = True
                    event_type = chunk.get("type")
                    if event_type == "content_delta":
                        delta = str(chunk.get("delta") or "")
                        content_seen = content_seen or bool(delta)
                        outgoing = handshake.consume(delta) if handshake else delta
                        if outgoing:
                            committed_parts.append(outgoing)
                            yielded = dict(chunk)
                            yielded["delta"] = outgoing
                            yield yielded
                    elif event_type == "tool_call_delta":
                        buffered_tool_events.append(dict(chunk))
                    elif event_type == "done":
                        done_event = dict(chunk)
                        with suppress(Exception):
                            await gen.aclose()
                        break
                    else:
                        yield chunk

                has_usage = _validate_tool_stream_completion(
                    done_event,
                    buffered_tool_events,
                    handshake,
                    content_seen,
                    usage_totals,
                    has_usage,
                )

                for tool_event in buffered_tool_events:
                    yield tool_event
                if done_event is not None:
                    if has_usage:
                        done_event["usage"] = dict(usage_totals)
                    if resume_attempt:
                        notes.append(f"流式响应已从检查点续传 {resume_attempt} 次")
                    done_event.setdefault("request_meta", request_meta(provider, model_name, notes))
                    yield done_event
                return
            except TimeoutError as exc:
                last_error = LLMError(f"流式请求超时（{timeout_seconds or '未限制'}秒）")
                error_cause = exc
            except LLMError as exc:
                last_error = exc
                error_cause = exc
                if (
                    safe_tool_choice is not None
                    and should_retry_without_tool_choice(exc)
                    and not raw_produced
                ):
                    notes.append("接口拒绝 tool_choice，已自动去掉该参数重试")
                    safe_tool_choice = None
                else:
                    non_retryable = _is_non_retryable(exc)
            except Exception as exc:
                last_error = LLMError(f"流式调用失败: {exc}")
                error_cause = exc
            finally:
                if gen is not None:
                    with suppress(Exception):
                        await gen.aclose()

            if non_retryable:
                raise last_error from error_cause
            if not raw_produced and raw_retries_remaining > 0:
                raw_retries_remaining -= 1
                await asyncio.sleep(min(8, (attempts - raw_retries_remaining) * 1.5))
                continue

            committed_text = "".join(committed_parts)
            can_resume = resumes_remaining > 0 and (
                raw_produced
                or handshake is not None
                or bool(committed_text)
                or bool(buffered_tool_events)
            )
            if can_resume:
                resumes_remaining -= 1
                resume_attempt += 1
                request_messages, handshake = _resume_messages(
                    messages,
                    committed_text,
                    tool_mode=True,
                )
                await _notify_stream_resume(
                    on_resume,
                    provider=provider,
                    resume_attempt=resume_attempt,
                    checkpoint_chars=len(committed_text),
                    tool_mode=True,
                )
                await asyncio.sleep(min(8, resume_attempt * 1.5))
                continue

            final_error = last_error or LLMError("流式请求失败，已达到最大重试次数")
            if error_cause is not None:
                raise final_error from error_cause
            raise final_error
