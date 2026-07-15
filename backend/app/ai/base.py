"""Base adapter interface for all LLM providers."""
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional


class BaseAdapter(ABC):
    """Abstract base class for LLM provider adapters."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        cli_command: Optional[str] = None,
        cli_args: Optional[str] = None,
        api_protocol: str = "chat_completions",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.cli_command = cli_command
        self.cli_args = cli_args
        self.api_protocol = api_protocol

    @abstractmethod
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
        """Non-streaming chat completion.

        Returns:
            {
                "content": str | None,
                "model": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
                "tool_calls": list[dict] | None
            }
            tool_calls entries: {"id": str, "type": "function", "function": {"name": str, "arguments": str}}
        """
        ...

    @abstractmethod
    async def stream_chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion — yields text token chunks only.

        This method is for text-only streaming. It does NOT surface tool calls.
        When tools are needed, use stream_chat_completion_with_tools() instead.
        """
        ...

    @abstractmethod
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
        """Streaming chat completion with tool call support.

        Yields chunks of the form:
            {"type": "content_delta", "delta": str}
            {"type": "tool_call_delta", "index": int, "id": str, "name": str | None, "arguments_delta": str}
            {"type": "done", "finish_reason": str, "usage": dict | None}
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        ...
