"""通义千问 (Qwen) adapter — uses OpenAI-compatible API format via DashScope."""
from typing import AsyncGenerator, Optional

from openai import APIError, APITimeoutError, APIConnectionError, AuthenticationError

from .base import BaseAdapter
from .openai_adapter import create_openai_compatible_client, _extract_tool_calls
from ..core.exceptions import LLMError


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
            kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if tools:
                kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

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
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"通义千问 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"通义千问 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
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
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except AuthenticationError as e:
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"通义千问 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"通义千问 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
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
            kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            if tools:
                kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

            stream = await client.chat.completions.create(**kwargs)
            tool_call_buffers: dict[int, dict] = {}
            finish_reason = None
            usage = None
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
                            yield {"type": "tool_call_delta", "index": idx, "id": buf["id"], "name": None, "arguments_delta": tc.function.arguments}
                        elif tc.id:
                            yield {"type": "tool_call_delta", "index": idx, "id": tc.id, "name": tc.function.name if tc.function else None, "arguments_delta": ""}
            yield {"type": "done", "finish_reason": finish_reason or "stop", "usage": usage}
        except AuthenticationError as e:
            raise LLMError(f"通义千问 API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"通义千问 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"通义千问 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"通义千问 API 错误: {e}")
        except Exception as e:
            raise LLMError(f"通义千问 流式调用失败: {e}")
