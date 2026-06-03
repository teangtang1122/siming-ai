"""Anthropic Claude adapter using the official anthropic SDK."""
from typing import AsyncGenerator, Optional

from anthropic import AsyncAnthropic, APIError, APITimeoutError, APIConnectionError, AuthenticationError

from .base import BaseAdapter
from ..core.exceptions import LLMError


def _convert_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI function-calling tool schemas to Anthropic tool format."""
    result = []
    for t in tools:
        func = t.get("function", t)
        anthropic_tool = {
            "name": func["name"],
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        }
        result.append(anthropic_tool)
    return result


def _convert_messages_for_anthropic(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    """Convert OpenAI-style messages to Anthropic format, handling all role types.

    Anthropic uses 'system' as a top-level param, 'user'/'assistant' in messages.
    Tool results go into user messages with tool_result content blocks.
    Tool calls from assistant messages go into assistant messages with tool_use blocks.
    """
    system_parts: list[str] = []
    anthropic_messages: list[dict] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            # System prompts — accumulate into the top-level system param
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue

        if role == "tool":
            # OpenAI tool result → Anthropic tool_result in a user message
            tool_call_id = msg.get("tool_call_id", "")
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content if isinstance(content, str) else str(content or ""),
                }],
            })
            continue

        if role == "assistant" and tool_calls:
            # Assistant message with tool calls → Anthropic assistant with tool_use blocks
            anthropic_content: list[dict] = []
            if content and isinstance(content, str) and content.strip():
                anthropic_content.append({"type": "text", "text": content})
            for tc in tool_calls:
                anthropic_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": _safe_json_loads(tc["function"]["arguments"]),
                })
            anthropic_messages.append({"role": "assistant", "content": anthropic_content})
            continue

        # Plain user/assistant messages
        if role in ("user", "assistant"):
            anthropic_messages.append({"role": role, "content": content or ""})
        else:
            anthropic_messages.append({"role": "user", "content": str(content or "")})

    if not anthropic_messages:
        anthropic_messages.append({"role": "user", "content": ""})

    system = "\n\n".join(system_parts) if system_parts else None
    return system, anthropic_messages


def _safe_json_loads(s: str) -> dict:
    import json as _json
    try:
        return _json.loads(s) if s else {}
    except _json.JSONDecodeError:
        return {}


def _parse_anthropic_response(response) -> tuple[str, list[dict] | None]:
    """Extract text content and tool_use blocks from an Anthropic response."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            import json as _json
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": _json.dumps(block.input, ensure_ascii=False) if isinstance(block.input, dict) else str(block.input),
                },
            })

    return "\n".join(text_parts), tool_calls or None


class AnthropicAdapter(BaseAdapter):
    """Adapter for Anthropic Claude API."""

    _convert_messages = staticmethod(_convert_messages_for_anthropic)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self) -> AsyncAnthropic:
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncAnthropic(**kwargs)

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
        system, anthropic_messages = _convert_messages_for_anthropic(messages)
        try:
            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = _convert_tools_to_anthropic(tools)
            # Map OpenAI tool_choice to Anthropic's equivalent where possible
            if tool_choice is not None:
                if tool_choice == "none":
                    # Anthropic doesn't support disabling tools once provided;
                    # just don't pass tools when not wanted
                    pass
                elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                    kwargs["tool_choice"] = {"type": "tool", "name": tool_choice["function"]["name"]}
                # "auto" is Anthropic's default — no parameter needed

            response = await client.messages.create(**kwargs)
            content_text, tool_calls = _parse_anthropic_response(response)
            return {
                "content": content_text or None,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens if response.usage else 0,
                    "completion_tokens": response.usage.output_tokens if response.usage else 0,
                    "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
                },
                "tool_calls": tool_calls,
            }
        except AuthenticationError as e:
            raise LLMError(f"Anthropic API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Anthropic 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Anthropic 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Anthropic API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Anthropic 调用失败: {e}")

    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Text-only streaming — no tool calls surfaced."""
        client = self._get_client()
        system, anthropic_messages = _convert_messages_for_anthropic(messages)
        try:
            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
            }
            if system:
                kwargs["system"] = system

            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
        except AuthenticationError as e:
            raise LLMError(f"Anthropic API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Anthropic 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Anthropic 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Anthropic API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Anthropic 流式调用失败: {e}")

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
        """Streaming chat completion that yields both text and tool call deltas.

        Uses Anthropic's streaming events: text_delta, content_block_start (tool_use),
        content_block_delta (input_json_delta).
        """
        import json as _json
        client = self._get_client()
        system, anthropic_messages = _convert_messages_for_anthropic(messages)
        try:
            kwargs = {
                "model": model,
                "messages": anthropic_messages,
                "temperature": temperature,
                "max_tokens": max_tokens or 4096,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = _convert_tools_to_anthropic(tools)
            if tool_choice is not None and isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                kwargs["tool_choice"] = {"type": "tool", "name": tool_choice["function"]["name"]}

            tool_index = 0
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "text_delta":
                        yield {"type": "content_delta", "delta": event.text}

                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            tool_index += 1  # 0-based → 1-based for compatibility
                            yield {
                                "type": "tool_call_delta",
                                "index": tool_index - 1,
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "arguments_delta": "",
                            }

                    elif event.type == "content_block_delta":
                        if event.delta.type == "input_json_delta":
                            yield {
                                "type": "tool_call_delta",
                                "index": tool_index - 1,
                                "id": "",  # id was sent in content_block_start
                                "name": None,
                                "arguments_delta": event.delta.partial_json,
                            }

                    elif event.type == "message_delta":
                        yield {
                            "type": "done",
                            "finish_reason": event.delta.stop_reason or "stop",
                            "usage": None,  # Anthropic streaming doesn't provide usage in-stream
                        }

        except AuthenticationError as e:
            raise LLMError(f"Anthropic API Key 无效: {e}")
        except APITimeoutError as e:
            raise LLMError(f"Anthropic 请求超时: {e}")
        except APIConnectionError as e:
            raise LLMError(f"Anthropic 连接错误: {e}")
        except APIError as e:
            raise LLMError(f"Anthropic API 错误: {e}")
        except Exception as e:
            raise LLMError(f"Anthropic 流式调用失败: {e}")
