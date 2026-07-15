"""OpenAI adapter using the official openai SDK."""
import json as _json
from typing import Any, AsyncGenerator, Optional
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


def _provider_extra_body(extra_body: Optional[dict]) -> dict | None:
    """Keep Siming orchestration metadata out of provider request bodies."""

    if not extra_body:
        return None
    payload = {
        key: value
        for key, value in extra_body.items()
        if not key.startswith(("moshu_", "local_cli_"))
    }
    return payload or None


def _responses_message_content(content: object) -> object:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    converted: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            converted.append({"type": "input_text", "text": str(part)})
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"text", "input_text"}:
            converted.append({"type": "input_text", "text": str(part.get("text") or "")})
            continue
        if part_type in {"image_url", "input_image"}:
            image = part.get("image_url")
            if isinstance(image, dict):
                image_url = image.get("url")
                detail = image.get("detail") or part.get("detail") or "auto"
            else:
                image_url = image
                detail = part.get("detail") or "auto"
            if image_url:
                converted.append({"type": "input_image", "image_url": image_url, "detail": detail})
            continue
        if part_type == "input_file":
            converted.append(dict(part))
            continue
        converted.append({"type": "input_text", "text": _json.dumps(part, ensure_ascii=False)})
    return converted


def _responses_input(messages: list[dict]) -> list[dict]:
    """Convert Chat Completions history into stateless Responses input items."""

    items: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            output = message.get("content")
            if not isinstance(output, str):
                output = _json.dumps(output, ensure_ascii=False)
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": output or "",
            })
            continue

        if role == "assistant":
            for state_item in message.get("provider_state") or []:
                if isinstance(state_item, dict) and state_item.get("type") == "reasoning":
                    items.append(dict(state_item))

        content = message.get("content")
        if content not in (None, "", []):
            items.append({
                "type": "message",
                "role": role if role in {"system", "developer", "user", "assistant"} else "user",
                "content": _responses_message_content(content),
            })

        if role != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            items.append({
                "type": "function_call",
                "call_id": str(tool_call.get("id") or tool_call.get("call_id") or ""),
                "name": str(function.get("name") or tool_call.get("name") or ""),
                "arguments": str(function.get("arguments") or tool_call.get("arguments") or "{}"),
            })
    return items


def _responses_tools(tools: Optional[list[dict]]) -> list[dict] | None:
    converted: list[dict] = []
    for tool in tools or []:
        if tool.get("type") != "function":
            converted.append(dict(tool))
            continue
        function = tool.get("function") or {}
        item = {
            "type": "function",
            "name": function.get("name"),
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            item["description"] = function["description"]
        if function.get("strict") is not None:
            item["strict"] = function["strict"]
        converted.append(item)
    return converted or None


def _responses_tool_choice(tool_choice: Optional[str | dict]) -> Optional[str | dict]:
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") != "function":
        return tool_choice
    function = tool_choice.get("function") or {}
    name = function.get("name") or tool_choice.get("name")
    return {"type": "function", "name": name} if name else "auto"


def _model_dump(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    return {}


def _responses_provider_state(response: object) -> list[dict]:
    state: list[dict] = []
    for item in getattr(response, "output", None) or []:
        dumped = _model_dump(item)
        if dumped.get("type") == "reasoning" and dumped.get("encrypted_content"):
            state.append(dumped)
    return state


def _responses_tool_calls(response: object) -> list[dict] | None:
    calls: list[dict] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        calls.append({
            "id": str(getattr(item, "call_id", None) or getattr(item, "id", "")),
            "type": "function",
            "function": {
                "name": str(getattr(item, "name", "")),
                "arguments": str(getattr(item, "arguments", "{}")),
            },
        })
    return calls or None


def _responses_usage(response: object) -> dict:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


class OpenAIAdapter(BaseAdapter):
    """Adapter for OpenAI API (GPT-4, GPT-4o, etc.)."""

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> AsyncOpenAI:
        return create_openai_compatible_client(self.api_key, self.base_url)

    def _responses_kwargs(
        self,
        *,
        messages: list[dict],
        model: str,
        max_tokens: Optional[int],
        extra_body: Optional[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
        stream: bool = False,
    ) -> dict:
        kwargs = compact_openai_kwargs({
            "model": model,
            "input": _responses_input(messages),
            "max_output_tokens": max_tokens,
            "tools": _responses_tools(tools),
            "tool_choice": _responses_tool_choice(tool_choice),
            "include": ["reasoning.encrypted_content"] if tools else None,
            "store": False,
            "stream": True if stream else None,
            "extra_body": _provider_extra_body(extra_body),
        })
        return kwargs

    async def _responses_chat_completion(
        self,
        *,
        messages: list[dict],
        model: str,
        max_tokens: Optional[int],
        extra_body: Optional[dict],
        tools: Optional[list[dict]],
        tool_choice: Optional[str | dict],
    ) -> dict:
        client = self._get_client()
        response = await client.responses.create(**self._responses_kwargs(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            extra_body=extra_body,
            tools=tools,
            tool_choice=tool_choice,
        ))
        return {
            "content": str(getattr(response, "output_text", "") or ""),
            "model": str(getattr(response, "model", model) or model),
            "usage": _responses_usage(response),
            "tool_calls": _responses_tool_calls(response),
            "provider_state": _responses_provider_state(response),
        }

    async def _stream_responses_completion(
        self,
        *,
        messages: list[dict],
        model: str,
        max_tokens: Optional[int],
        extra_body: Optional[dict],
        tools: Optional[list[dict]],
        tool_choice: Optional[str | dict],
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()
        stream = await client.responses.create(**self._responses_kwargs(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            extra_body=extra_body,
            tools=tools,
            tool_choice=tool_choice,
            stream=True,
        ))
        tool_buffers: dict[int, dict[str, Any]] = {}
        completed = False

        async for event in stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "response.output_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                if delta:
                    yield {"type": "content_delta", "delta": delta}
                continue
            if event_type == "response.reasoning_summary_text.delta":
                delta = str(getattr(event, "delta", "") or "")
                if delta:
                    yield {"type": "reasoning_delta", "delta": delta}
                continue
            if event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    index = int(getattr(event, "output_index", len(tool_buffers)) or 0)
                    buffer = {
                        "id": str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                        "name": str(getattr(item, "name", "") or ""),
                        "arguments": "",
                    }
                    tool_buffers[index] = buffer
                    yield {
                        "type": "tool_call_delta",
                        "index": index,
                        "id": buffer["id"],
                        "name": buffer["name"],
                        "arguments_delta": "",
                    }
                continue
            if event_type == "response.function_call_arguments.delta":
                index = int(getattr(event, "output_index", 0) or 0)
                delta = str(getattr(event, "delta", "") or "")
                buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
                buffer["arguments"] += delta
                if delta:
                    yield {
                        "type": "tool_call_delta",
                        "index": index,
                        "id": buffer["id"],
                        "name": None,
                        "arguments_delta": delta,
                    }
                continue
            if event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    index = int(getattr(event, "output_index", 0) or 0)
                    buffer = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    call_id = str(getattr(item, "call_id", None) or getattr(item, "id", ""))
                    name = str(getattr(item, "name", "") or "")
                    arguments = str(getattr(item, "arguments", "") or "")
                    if (call_id and not buffer["id"]) or (name and not buffer["name"]):
                        buffer["id"] = buffer["id"] or call_id
                        buffer["name"] = buffer["name"] or name
                        yield {
                            "type": "tool_call_delta",
                            "index": index,
                            "id": buffer["id"],
                            "name": buffer["name"],
                            "arguments_delta": "",
                        }
                    if arguments and not buffer["arguments"]:
                        buffer["arguments"] = arguments
                        yield {
                            "type": "tool_call_delta",
                            "index": index,
                            "id": buffer["id"],
                            "name": None,
                            "arguments_delta": arguments,
                        }
                continue
            if event_type == "response.failed":
                response = getattr(event, "response", None)
                error = getattr(response, "error", None)
                raise LLMError(str(getattr(error, "message", None) or error or "Responses API generation failed"))
            if event_type == "response.completed":
                response = getattr(event, "response", None)
                completed = True
                yield {
                    "type": "done",
                    "finish_reason": str(getattr(response, "status", "completed") or "completed"),
                    "usage": _responses_usage(response),
                    "provider_state": _responses_provider_state(response),
                }

        if not completed:
            yield {"type": "done", "finish_reason": "stop", "usage": None, "provider_state": []}

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
        try:
            if self.api_protocol == "responses":
                return await self._responses_chat_completion(
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            client = self._get_client()
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
            provider_body = _provider_extra_body(extra_body)
            if provider_body:
                kwargs["extra_body"] = provider_body

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
        try:
            if self.api_protocol == "responses":
                async for chunk in self._stream_responses_completion(
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    tools=None,
                    tool_choice=None,
                ):
                    if chunk.get("type") == "content_delta":
                        yield str(chunk.get("delta") or "")
                return
            client = self._get_client()
            kwargs = compact_openai_kwargs(dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ))
            provider_body = _provider_extra_body(extra_body)
            if provider_body:
                kwargs["extra_body"] = provider_body
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
        try:
            if self.api_protocol == "responses":
                async for chunk in self._stream_responses_completion(
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    tools=tools,
                    tool_choice=tool_choice,
                ):
                    yield chunk
                return
            client = self._get_client()
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
            provider_body = _provider_extra_body(extra_body)
            if provider_body:
                kwargs["extra_body"] = provider_body

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
