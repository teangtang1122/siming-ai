"""Anthropic Claude adapter using the official anthropic SDK."""
import json
from typing import Any, AsyncGenerator, Optional

from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
)

from ..core.exceptions import LLMError
from .base import BaseAdapter


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


def _anthropic_continuation_blocks(message: dict) -> list[dict]:
    """Return provider-native thinking blocks that can be replayed verbatim."""

    blocks: list[dict] = []
    for item in message.get("provider_state") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"thinking", "redacted_thinking"}:
            blocks.append(dict(item))
    return blocks


def _convert_messages_for_anthropic(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    """Convert OpenAI-style messages to Anthropic format, handling all role types.

    Anthropic uses 'system' as a top-level param, 'user'/'assistant' in messages.
    Tool results go into user messages with tool_result content blocks.
    Tool calls from assistant messages go into assistant messages with tool_use blocks.
    """
    system_parts: list[str] = []
    anthropic_messages: list[dict] = []

    index = 0
    while index < len(messages):
        msg = messages[index]
        role = msg.get("role")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            # System prompts — accumulate into the top-level system param
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            index += 1
            continue

        if role == "tool":
            # One assistant may request several tools in parallel. Anthropic's
            # native transaction represents the complete consecutive result
            # batch as one user message containing ordered tool_result blocks.
            result_blocks: list[dict] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                result_message = messages[index]
                tool_call_id = result_message.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise ValueError("Anthropic tool result requires a non-empty native call ID")
                result_content = result_message.get("content")
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": (
                        result_content
                        if isinstance(result_content, str)
                        else str(result_content or "")
                    ),
                })
                index += 1
            anthropic_messages.append({"role": "user", "content": result_blocks})
            continue

        if role == "assistant" and tool_calls:
            # Assistant message with tool calls → Anthropic assistant with tool_use blocks
            anthropic_content = _anthropic_continuation_blocks(msg)
            if content and isinstance(content, str) and content.strip():
                anthropic_content.append({"type": "text", "text": content})
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    raise ValueError("Anthropic tool call must be an object")
                function = tc.get("function")
                if not isinstance(function, dict):
                    raise ValueError("Anthropic tool call requires a function object")
                call_id = tc.get("id")
                name = function.get("name")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("Anthropic tool call requires a non-empty native call ID")
                if not isinstance(name, str) or not name:
                    raise ValueError("Anthropic tool call requires a non-empty function name")
                anthropic_content.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": _safe_json_loads(function.get("arguments")),
                })
            anthropic_messages.append({"role": "assistant", "content": anthropic_content})
            index += 1
            continue

        # Plain user/assistant messages
        if role in ("user", "assistant"):
            continuation = _anthropic_continuation_blocks(msg) if role == "assistant" else []
            if continuation:
                if content and isinstance(content, str):
                    continuation.append({"type": "text", "text": content})
                anthropic_messages.append({"role": role, "content": continuation})
            else:
                anthropic_messages.append({"role": role, "content": content or ""})
        else:
            anthropic_messages.append({"role": "user", "content": str(content or "")})
        index += 1

    if not anthropic_messages:
        anthropic_messages.append({"role": "user", "content": ""})

    system = "\n\n".join(system_parts) if system_parts else None
    return system, anthropic_messages


def _safe_json_loads(s: Any) -> dict:
    """Decode a native call without repairing invalid provider output."""

    if not isinstance(s, str):
        raise ValueError("Anthropic tool call arguments must be a JSON string")
    try:
        value = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError("Anthropic tool call arguments must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Anthropic tool call arguments must be a JSON object")
    return value


def _dump_anthropic_block(block: object) -> dict[str, Any]:
    if isinstance(block, dict):
        return dict(block)
    model_dump = getattr(block, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _parse_anthropic_response(
    response: object,
) -> tuple[str, list[dict] | None, str, list[dict[str, Any]]]:
    """Extract text content and tool_use blocks from an Anthropic response."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    provider_state: list[dict[str, Any]] = []
    tool_calls: list[dict] = []

    for block in getattr(response, "content", None) or []:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type in {"thinking", "redacted_thinking"}:
            dumped = _dump_anthropic_block(block)
            if dumped:
                provider_state.append(dumped)
            thinking = getattr(block, "thinking", None)
            if thinking:
                reasoning_parts.append(str(thinking))
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": (
                        json.dumps(block.input, ensure_ascii=False)
                        if isinstance(block.input, dict)
                        else str(block.input)
                    ),
                },
            })

    return (
        "\n".join(text_parts),
        tool_calls or None,
        "\n".join(reasoning_parts),
        provider_state,
    )


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
            (
                content_text,
                tool_calls,
                reasoning_content,
                provider_state,
            ) = _parse_anthropic_response(response)
            return {
                "content": content_text or None,
                "reasoning_content": reasoning_content,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens if response.usage else 0,
                    "completion_tokens": response.usage.output_tokens if response.usage else 0,
                    "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
                },
                "tool_calls": tool_calls,
                "provider_state": provider_state,
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
        self.last_stream_finish_reason = None
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
                final_message = await stream.get_final_message()
                self.last_stream_finish_reason = str(
                    getattr(final_message, "stop_reason", None) or "stop"
                )
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
            finish_reason = "incomplete"
            usage = None
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
                        elif event.delta.type == "thinking_delta":
                            thinking = str(getattr(event.delta, "thinking", "") or "")
                            if thinking:
                                yield {"type": "reasoning_delta", "delta": thinking}

                    elif event.type == "message_delta":
                        finish_reason = str(event.delta.stop_reason or "stop")

                final_message = await stream.get_final_message()
                _, _, _, provider_state = _parse_anthropic_response(final_message)
                final_usage = getattr(final_message, "usage", None)
                if final_usage is not None:
                    input_tokens = int(getattr(final_usage, "input_tokens", 0) or 0)
                    output_tokens = int(getattr(final_usage, "output_tokens", 0) or 0)
                    usage = {
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    }
                yield {
                    "type": "done",
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "provider_state": provider_state,
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
