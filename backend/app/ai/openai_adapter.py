"""OpenAI adapter using the official openai SDK."""
import json as _json
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI, APIError, APITimeoutError, APIConnectionError, AuthenticationError

from .base import BaseAdapter
from ..core.exceptions import LLMError


class OpenAIClientProxy:
    """Proxy client that keeps SDK behavior and exposes a stable base_url string."""

    def __init__(self, client: AsyncOpenAI, base_url: str):
        self._client = client
        self.base_url = base_url.rstrip("/")

    def __getattr__(self, name: str):
        return getattr(self._client, name)


def create_openai_compatible_client(api_key: str, base_url: Optional[str] = None):
    """Create an AsyncOpenAI-compatible client with normalized display metadata."""
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
        if _is_loopback_url(base_url):
            kwargs["http_client"] = httpx.AsyncClient(trust_env=False)
    client = AsyncOpenAI(**kwargs)
    if base_url:
        return OpenAIClientProxy(client, base_url)
    return client


def _is_loopback_url(base_url: str) -> bool:
    try:
        host = urlparse(base_url).hostname
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _extract_tool_calls(message) -> list[dict] | None:
    """Extract and normalize tool calls from an OpenAI message object."""
    if not message or not message.tool_calls:
        return None
    result = []
    for tc in message.tool_calls:
        result.append({
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        })
    return result or None


def compact_openai_kwargs(kwargs: dict) -> dict:
    """Remove unset top-level parameters before sending OpenAI-compatible JSON."""
    return {key: value for key, value in kwargs.items() if value is not None}


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI API (GPT-4, GPT-4o, etc.)."""

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> AsyncOpenAI:
        return create_openai_compatible_client(self.api_key, self.base_url)

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
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "tool_calls": _extract_tool_calls(choice.message),
            }
        except AuthenticationError as e:
            raise LLMError(f"OpenAI API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"OpenAI 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"OpenAI 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"OpenAI API 错误: {e}")
        except Exception as e:
            raise LLMError(f"OpenAI 调用失败: {e}")

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Text-only streaming — no tool calls surfaced. Use stream_chat_completion_with_tools for tools."""
        client = self._get_client()
        try:
            kwargs = compact_openai_kwargs(dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ))
            if extra_body:
                kwargs["extra_body"] = extra_body
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except AuthenticationError as e:
            raise LLMError(f"OpenAI API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"OpenAI 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"OpenAI 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"OpenAI API 错误: {e}")
        except Exception as e:
            raise LLMError(f"OpenAI 流式调用失败: {e}")

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
        """Streaming chat completion that yields both text and tool call deltas."""
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
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            if extra_body:
                kwargs["extra_body"] = extra_body

            stream = await client.chat.completions.create(**kwargs)
            # Track tool call accumulation across chunks
            tool_call_buffers: dict[int, dict] = {}
            finish_reason = None
            usage = None

            async for chunk in stream:
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason or finish_reason

                # Track usage from final chunk
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

                # Text content delta
                if delta.content:
                    yield {"type": "content_delta", "delta": delta.content}

                # Tool call deltas
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
                            # Initial chunk with just the id
                            yield {
                                "type": "tool_call_delta",
                                "index": idx,
                                "id": tc.id,
                                "name": tc.function.name if tc.function else None,
                                "arguments_delta": "",
                            }

            yield {"type": "done", "finish_reason": finish_reason or "stop", "usage": usage}

        except AuthenticationError as e:
            raise LLMError(f"OpenAI API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"OpenAI 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"OpenAI 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"OpenAI API 错误: {e}")
        except Exception as e:
            raise LLMError(f"OpenAI 流式调用失败: {e}")
