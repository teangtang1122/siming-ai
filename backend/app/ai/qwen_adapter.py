"""通义千问 (Qwen) adapter — uses OpenAI-compatible API format via DashScope."""
from typing import AsyncGenerator, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError

from ..core.exceptions import LLMError
from .base import BaseAdapter
from .transport_timeouts import model_timeout_error, model_connection_error
from .completion_status import require_complete_response
from .openai_adapter import (
    _extract_tool_calls,
    compact_openai_kwargs,
    create_openai_compatible_client,
    message_reasoning_content,
    normalize_openai_tool_call_delta,
)
from .request_policy import provider_extra_body, provider_tool_choice, provider_request_kwargs


class QwenAdapter(BaseAdapter):
    """Adapter for 通义千问 (Qwen) API via DashScope (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def provider_name(self) -> str:
        return "qwen"

    def _get_client(self):
        return create_openai_compatible_client(
            self.api_key,
            self.base_url or self.DEFAULT_BASE_URL,
        )

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
        try:
            kwargs = compact_openai_kwargs(dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ))
            if tools:
                kwargs["tools"] = tools
            tool_choice = provider_tool_choice(
                self.base_url or self.DEFAULT_BASE_URL, "chat_completions", tool_choice,
            )
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            provider_body = provider_extra_body(
                extra_body, self.base_url or self.DEFAULT_BASE_URL, text_only=not tools,
            )
            if provider_body:
                kwargs["extra_body"] = provider_body

            response = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url or self.DEFAULT_BASE_URL, "chat_completions"))
            choice = response.choices[0]
            require_complete_response(getattr(choice, "finish_reason", None), max_tokens)
            return {
                "content": choice.message.content or "",
                "reasoning_content": message_reasoning_content(choice.message),
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "tool_calls": _extract_tool_calls(choice.message),
            }
        except AuthenticationError as e:
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"通义千问 调用失败: {e}")

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Text-only streaming."""
        client = self._get_client()
        self.last_stream_finish_reason = None
        try:
            kwargs = compact_openai_kwargs(dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ))
            provider_body = provider_extra_body(
                extra_body, self.base_url or self.DEFAULT_BASE_URL, text_only=True,
            )
            if provider_body:
                kwargs["extra_body"] = provider_body

            stream = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url or self.DEFAULT_BASE_URL, "chat_completions"))
            async for chunk in stream:
                yield ""
                finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                if finish_reason:
                    self.last_stream_finish_reason = str(finish_reason)
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            self.last_stream_finish_reason = self.last_stream_finish_reason or "incomplete"
        except AuthenticationError as e:
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"通义千问 流式调用失败: {e}")

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
        try:
            kwargs = compact_openai_kwargs(dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ))
            if tools:
                kwargs["tools"] = tools
            tool_choice = provider_tool_choice(
                self.base_url or self.DEFAULT_BASE_URL, "chat_completions", tool_choice,
            )
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            provider_body = provider_extra_body(
                extra_body, self.base_url or self.DEFAULT_BASE_URL, text_only=not tools,
            )
            if provider_body:
                kwargs["extra_body"] = provider_body

            stream = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url or self.DEFAULT_BASE_URL, "chat_completions"))
            tool_call_buffers: dict[int, dict] = {}
            finish_reason = None
            usage = None
            reasoning_buffer = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason or finish_reason
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
                if delta.content:
                    yield {"type": "content_delta", "delta": delta.content}
                reasoning = message_reasoning_content(delta)
                if reasoning:
                    reasoning_buffer += reasoning
                    yield {"type": "reasoning_delta", "delta": reasoning}
                if delta.tool_calls:
                    for tc in delta.tool_calls:
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
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"通义千问 流式调用失败: {e}")
