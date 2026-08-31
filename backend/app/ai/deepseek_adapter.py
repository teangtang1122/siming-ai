"""DeepSeek adapter — uses OpenAI-compatible API format."""
from typing import AsyncGenerator, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError

from ..core.exceptions import LLMError
from ..core.provider_model_identity import (
    DEEPSEEK_SUPPORTED_MODELS,
    canonical_model_name,
)
from .base import BaseAdapter
from .openai_adapter import (
    _extract_tool_calls,
    compact_openai_kwargs,
    create_openai_compatible_client,
    normalize_openai_tool_call_delta,
)


def _provider_extra_body(extra_body: Optional[dict]) -> dict:
    return {
        key: value
        for key, value in (extra_body or {}).items()
        if not key.startswith(("moshu_", "local_cli_"))
    }


def _plain_text_extra_body(extra_body: Optional[dict]) -> dict:
    """Reserve the response budget for content on text-only adapter methods.

    DeepSeek V4 defaults thinking mode to enabled. These adapter methods expose
    only final text, not reasoning deltas, so an implicit thinking response can
    consume the output budget without producing any usable content. Callers
    that deliberately handle that protocol can still opt in explicitly.
    """
    body = _provider_extra_body(extra_body)
    body.setdefault("thinking", {"type": "disabled"})
    return body


class DeepSeekAdapter(BaseAdapter):
    """Adapter for DeepSeek API (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    SUPPORTED_MODELS = DEEPSEEK_SUPPORTED_MODELS

    @property
    def provider_name(self) -> str:
        return "deepseek"

    def _get_client(self):
        return create_openai_compatible_client(
            self.api_key,
            self.base_url or self.DEFAULT_BASE_URL,
        )

    def _normalize_model(self, model: str) -> str:
        normalized = canonical_model_name(self.provider_name, model)
        if normalized.startswith("deepseek-") and normalized not in self.SUPPORTED_MODELS:
            supported = "、".join(sorted(self.SUPPORTED_MODELS))
            raise LLMError(f"DeepSeek 当前支持的模型为 {supported}，请在系统设置中重新选择")
        return normalized

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
        client = self._get_client()
        model = self._normalize_model(model)
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens))
        kwargs["extra_body"] = _plain_text_extra_body(extra_body)
        if tools:
            kwargs["tools"] = tools
        # DeepSeek V4 thinking mode rejects OpenAI's tool_choice parameter even
        # though it accepts the tools array. Let the model choose tools from the
        # prompt instead of forcing the choice at API level.
        try:
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""
            if not content and getattr(choice.message, "reasoning_content", None):
                raise LLMError(
                    "DeepSeek 已返回思考内容，但最终回答为空。系统可提高输出上限或使用无思考补偿重试；原始思考模式无需永久关闭。"
                )
            return {
                "content": content,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "tool_calls": _extract_tool_calls(choice.message),
            }
        except AuthenticationError as e:
            raise LLMError(f"DeepSeek API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"DeepSeek 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"DeepSeek 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except Exception as e:
            raise LLMError(f"DeepSeek 调用失败: {e}")

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        model = self._normalize_model(model)
        self.last_stream_finish_reason = None
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True))
        kwargs["extra_body"] = _plain_text_extra_body(extra_body)
        try:
            stream = await client.chat.completions.create(**kwargs)
            content_emitted = False
            reasoning_received = False
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                if getattr(choices[0], "finish_reason", None):
                    self.last_stream_finish_reason = str(choices[0].finish_reason)
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                reasoning_received = reasoning_received or bool(
                    getattr(delta, "reasoning_content", None)
                )
                content = getattr(delta, "content", None)
                if content:
                    content_emitted = True
                    yield content
            self.last_stream_finish_reason = self.last_stream_finish_reason or "incomplete"
            if not content_emitted and reasoning_received:
                raise LLMError(
                    "DeepSeek 已返回思考内容，但最终回答为空。系统可提高输出上限或使用无思考补偿重试；原始思考模式无需永久关闭。"
                )
        except AuthenticationError as e:
            raise LLMError(f"DeepSeek API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"DeepSeek 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"DeepSeek 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except Exception as e:
            raise LLMError(f"DeepSeek 流式调用失败: {e}")

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
        client = self._get_client()
        model = self._normalize_model(model)
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True))
        provider_body = _provider_extra_body(extra_body)
        if provider_body:
            kwargs["extra_body"] = provider_body
        if tools:
            kwargs["tools"] = tools
        # DeepSeek V4 thinking mode rejects OpenAI's tool_choice parameter even
        # though it accepts the tools array. Let the model choose tools from the
        # prompt instead of forcing the choice at API level.
        try:
            stream = await client.chat.completions.create(**kwargs)
            tool_call_buffers: dict[int, dict] = {}
            finish_reason = None
            usage = None
            reasoning_buffer = ""
            async for chunk in stream:
                if getattr(chunk, 'usage', None):
                    u = chunk.usage
                    if isinstance(u, dict):
                        usage = {
                            "prompt_tokens": u.get("prompt_tokens", 0),
                            "completion_tokens": u.get("completion_tokens", 0),
                            "total_tokens": u.get("total_tokens", 0),
                        }
                    else:
                        usage = {
                            "prompt_tokens": getattr(u, 'prompt_tokens', 0),
                            "completion_tokens": getattr(u, 'completion_tokens', 0),
                            "total_tokens": getattr(u, 'total_tokens', 0),
                        }
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                if delta is None:
                    continue
                rc = getattr(delta, 'reasoning_content', None)
                if rc:
                    reasoning_buffer += rc
                    yield {"type": "reasoning_delta", "delta": rc}
                content = getattr(delta, "content", None)
                if content:
                    yield {"type": "content_delta", "delta": content}
                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        event = normalize_openai_tool_call_delta(tc, tool_call_buffers)
                        if event:
                            yield event
            yield {
                "type": "done",
                "finish_reason": finish_reason or "incomplete",
                "usage": usage,
                "reasoning_content": reasoning_buffer,
            }
        except AuthenticationError as e:
            raise LLMError(f"DeepSeek API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"DeepSeek 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"DeepSeek 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except Exception as e:
            raise LLMError(f"DeepSeek 流式调用失败: {e}")
