"""DeepSeek adapter — uses OpenAI-compatible API format."""
from typing import AsyncGenerator, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError

from ..core.exceptions import LLMError
from ..core.provider_model_identity import canonical_model_name
from .openai_adapter import (
    OpenAIAdapter,
    _extract_tool_calls,
    compact_openai_kwargs,
    create_openai_compatible_client,
    message_reasoning_content,
    normalize_openai_tool_call_delta,
)
from .completion_status import require_complete_response
from .request_policy import provider_extra_body, provider_tool_choice, provider_request_kwargs
from .transport_timeouts import model_timeout_error, model_connection_error


class DeepSeekAdapter(OpenAIAdapter):
    """Adapter for DeepSeek API (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str, base_url: str | None = None, **kwargs):
        super().__init__(api_key=api_key, base_url=base_url or self.DEFAULT_BASE_URL, **kwargs)

    @property
    def provider_name(self) -> str:
        return "deepseek"

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
        if self.api_protocol == "responses":
            return await super().chat_completion(
                messages, model, temperature, max_tokens, extra_body, tools, tool_choice,
            )
        client = self._get_client()
        model = canonical_model_name(self.provider_name, model)
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens))
        provider_body = provider_extra_body(extra_body, self.base_url, text_only=not tools)
        if provider_body:
            kwargs["extra_body"] = provider_body
        if tools:
            kwargs["tools"] = tools
        tool_choice = provider_tool_choice(self.base_url, "chat_completions", tool_choice)
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        try:
            response = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url, "chat_completions"))
            choice = response.choices[0]
            require_complete_response(getattr(choice, "finish_reason", None), max_tokens)
            content = choice.message.content or ""
            reasoning_content = message_reasoning_content(choice.message)
            tool_calls = _extract_tool_calls(choice.message)
            if not content and reasoning_content and not tool_calls:
                raise LLMError(
                    "DeepSeek 已返回思考内容，但最终回答为空。系统可提高输出上限或使用无思考补偿重试；原始思考模式无需永久关闭。"
                )
            return {
                "content": content,
                "reasoning_content": reasoning_content,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "tool_calls": tool_calls,
            }
        except AuthenticationError as e:
            raise LLMError(f"DeepSeek API Key 无效: {e}")
        except APITimeoutError as e:
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except LLMError:
            raise
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
        if self.api_protocol == "responses":
            async for item in super().stream_chat_completion(
                messages, model, temperature, max_tokens, extra_body,
            ):
                yield item
            return
        client = self._get_client()
        model = canonical_model_name(self.provider_name, model)
        self.last_stream_finish_reason = None
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True))
        provider_body = provider_extra_body(extra_body, self.base_url, text_only=True)
        if provider_body:
            kwargs["extra_body"] = provider_body
        try:
            stream = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url, "chat_completions"))
            content_emitted = False
            reasoning_received = False
            async for chunk in stream:
                yield ""
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
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except LLMError:
            raise
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
        if self.api_protocol == "responses":
            async for event in super().stream_chat_completion_with_tools(
                messages, model, temperature, max_tokens, extra_body, tools, tool_choice,
            ):
                yield event
            return
        client = self._get_client()
        model = canonical_model_name(self.provider_name, model)
        kwargs = compact_openai_kwargs(dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True))
        provider_body = provider_extra_body(extra_body, self.base_url, text_only=not tools)
        if provider_body:
            kwargs["extra_body"] = provider_body
        if tools:
            kwargs["tools"] = tools
        tool_choice = provider_tool_choice(self.base_url, "chat_completions", tool_choice)
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        try:
            stream = await client.chat.completions.create(**provider_request_kwargs(kwargs, self.base_url, "chat_completions"))
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
            raise model_timeout_error(e) from e
        except APIConnectionError as e:
            raise model_connection_error(e) from e
        except APIError as e:
            raise LLMError(f"DeepSeek API 错误: {e}")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"DeepSeek 流式调用失败: {e}")
