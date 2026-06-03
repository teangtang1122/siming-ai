"""Provider capability metadata used by the LLM gateway.

The adapters intentionally stay thin. Provider quirks that affect request
shape live here so caller code does not need to know which APIs reject which
parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_tools: bool = True
    supports_tool_choice: bool = True
    supports_streaming_tools: bool = True


PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "openai": ProviderCapabilities(),
    "qwen": ProviderCapabilities(),
    "anthropic": ProviderCapabilities(),
    # DeepSeek V4 thinking mode accepts tools but rejects OpenAI tool_choice.
    "deepseek": ProviderCapabilities(supports_tool_choice=False),
    # Gemini OpenAI-compatible endpoint can reject tool_choice when the selected
    # model is in thinking mode. Leaving tools present still lets it call tools.
    "gemini": ProviderCapabilities(supports_tool_choice=False),
}


def provider_capabilities(provider: str) -> ProviderCapabilities:
    return PROVIDER_CAPABILITIES.get(provider, ProviderCapabilities())


def sanitize_tool_request(
    provider: str,
    tools: list[dict] | None,
    tool_choice: str | dict | None,
) -> tuple[list[dict] | None, str | dict | None, list[str]]:
    """Return provider-safe tool arguments plus human-readable adjustments."""
    caps = provider_capabilities(provider)
    notes: list[str] = []
    safe_tools = tools
    safe_tool_choice = tool_choice

    if tools and not caps.supports_tools:
        safe_tools = None
        safe_tool_choice = None
        notes.append(f"{provider} 不支持工具调用，已改用普通对话")
    elif tool_choice is not None and not caps.supports_tool_choice:
        safe_tool_choice = None
        notes.append(f"{provider} 不支持当前 tool_choice 参数，已让模型自动选择工具")

    return safe_tools, safe_tool_choice, notes


def should_retry_without_tool_choice(error: BaseException) -> bool:
    """Detect provider errors caused specifically by tool_choice."""
    text = str(error).lower()
    return "tool_choice" in text or "tool choice" in text


def normalize_retry_count(retry: int | None) -> int:
    """Treat retry as extra attempts, but always make at least one attempt.

    Existing call sites often pass retry=0 intending "do not retry"; the old
    gateway accidentally made zero attempts. This helper preserves the intent:
    retry=0 means one attempt, retry=1 means two attempts, etc.
    """
    try:
        value = int(retry or 0)
    except (TypeError, ValueError):
        value = 0
    return max(1, value + 1)


def request_meta(provider: str, model: str, notes: list[str]) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "adjustments": notes,
    }
