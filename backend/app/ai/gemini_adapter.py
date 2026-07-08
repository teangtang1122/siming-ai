"""Gemini adapter using Google's OpenAI-compatible API endpoint."""
from typing import AsyncGenerator, Optional

from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError

from .base import BaseAdapter
from .openai_adapter import compact_openai_kwargs, create_openai_compatible_client, _extract_tool_calls
from ..core.exceptions import LLMError


class GeminiAdapter(BaseAdapter):
    """Adapter for Google Gemini through the OpenAI-compatible API."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _get_client(self):
        return create_openai_compatible_client(
            self.api_key,
            self.base_url or self.DEFAULT_BASE_URL,
        )

    @staticmethod
    def _normalize_model(model: str) -> str:
        return model.removeprefix("models/")

    @staticmethod
    def _usage_payload(usage) -> dict:
        if not usage:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if isinstance(usage, dict):
            return {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

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
        kwargs = compact_openai_kwargs(dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ))
        if extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        try:
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": response.model,
                "usage": self._usage_payload(response.usage),
                "tool_calls": _extract_tool_calls(choice.message),
            }
        except AuthenticationError as e:
            raise LLMError(f"Gemini API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Gemini 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Gemini 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Gemini API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Gemini 调用失败: {e}")

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
        kwargs = compact_openai_kwargs(dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        ))
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except AuthenticationError as e:
            raise LLMError(f"Gemini API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Gemini 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Gemini 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Gemini API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Gemini 流式调用失败: {e}")

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
        kwargs = compact_openai_kwargs(dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        ))
        if extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        try:
            stream = await client.chat.completions.create(**kwargs)
            tool_call_buffers: dict[int, dict] = {}
            finish_reason = None
            usage = None
            async for chunk in stream:
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason or finish_reason
                if getattr(chunk, "usage", None):
                    usage = self._usage_payload(chunk.usage)
                if delta.content:
                    yield {"type": "content_delta", "delta": delta.content}
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_buffers:
                            tool_call_buffers[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                        buf = tool_call_buffers[idx]
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function and tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            buf["arguments"] += tc.function.arguments
                            yield {
                                "type": "tool_call_delta",
                                "index": idx,
                                "id": buf["id"],
                                "name": None,
                                "arguments_delta": tc.function.arguments,
                            }
                        elif tc.id:
                            yield {
                                "type": "tool_call_delta",
                                "index": idx,
                                "id": tc.id,
                                "name": tc.function.name if tc.function else None,
                                "arguments_delta": "",
                            }
            yield {"type": "done", "finish_reason": finish_reason or "stop", "usage": usage}
        except AuthenticationError as e:
            raise LLMError(f"Gemini API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Gemini 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Gemini 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Gemini API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Gemini 流式调用失败: {e}")
