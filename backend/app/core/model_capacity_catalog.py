"""Versioned provider/model capacity facts from primary provider documentation.

Only exact model identifiers belong here.  Unknown aliases and custom
OpenAI-compatible deployments deliberately remain unverified so the context
runtime never turns a name pattern into a safety claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .provider_model_identity import canonical_model_identity, canonical_provider


@dataclass(frozen=True)
class KnownModelCapacity:
    context_window_tokens: int
    max_output_tokens: int
    source: str

    def __post_init__(self) -> None:
        if self.context_window_tokens <= 0 or self.max_output_tokens <= 0:
            raise ValueError("model capacity values must be positive")
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("model output limit must be smaller than its context window")


def _capacity(window: int, output: int, source: str) -> KnownModelCapacity:
    return KnownModelCapacity(window, output, source)


_OPENAI = "openai_model_docs_2026_08_30"
_ANTHROPIC = "anthropic_model_docs_2026_08_30"
_DEEPSEEK = "deepseek_model_docs_2026_08_30"
_GEMINI = "gemini_model_docs_2026_08_30"
_QWEN = "qwen_model_docs_2026_08_30"

_CAPACITIES: dict[tuple[str, str], KnownModelCapacity] = {
    # OpenAI model pages: https://developers.openai.com/api/docs/models
    **{
        ("openai", model): _capacity(1_050_000, 128_000, _OPENAI)
        for model in (
            "gpt-5.6",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5.4",
            "gpt-5.4-pro",
        )
    },
    **{
        ("openai", model): _capacity(400_000, 128_000, _OPENAI)
        for model in (
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5.1",
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5.2-codex",
            "gpt-5.3-codex",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
        )
    },
    **{
        ("openai", model): _capacity(1_047_576, 32_768, _OPENAI)
        for model in (
            "gpt-4.1",
            "gpt-4.1-2025-04-14",
            "gpt-4.1-mini",
            "gpt-4.1-mini-2025-04-14",
            "gpt-4.1-nano",
            "gpt-4.1-nano-2025-04-14",
        )
    },
    **{
        ("openai", model): _capacity(128_000, 16_384, _OPENAI)
        for model in (
            "gpt-4o",
            "gpt-4o-2024-05-13",
            "gpt-4o-2024-08-06",
            "gpt-4o-2024-11-20",
            "gpt-4o-mini",
            "gpt-4o-mini-2024-07-18",
        )
    },
    # Anthropic Models API/docs: https://platform.claude.com/docs/en/models/overview
    **{
        ("anthropic", model): _capacity(1_000_000, 128_000, _ANTHROPIC)
        for model in (
            "claude-fable-5",
            "claude-mythos-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
        )
    },
    ("anthropic", "claude-haiku-4-5-20251001"): _capacity(
        200_000, 64_000, _ANTHROPIC
    ),
    ("anthropic", "claude-3-7-sonnet-20250219"): _capacity(
        200_000, 64_000, _ANTHROPIC
    ),
    ("anthropic", "claude-3-5-sonnet-20241022"): _capacity(
        200_000, 8_192, _ANTHROPIC
    ),
    # DeepSeek V4 pricing/specification page.
    ("deepseek", "deepseek-v4-pro"): _capacity(
        1_000_000, 384_000, _DEEPSEEK
    ),
    ("deepseek", "deepseek-v4-flash"): _capacity(
        1_000_000, 384_000, _DEEPSEEK
    ),
    # Google Gemini model pages.
    **{
        ("gemini", model): _capacity(1_048_576, 65_536, _GEMINI)
        for model in (
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-pro-preview",
            "gemini-3.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        )
    },
    # Alibaba Cloud Model Studio model pages.
    ("qwen", "qwen-max"): _capacity(32_768, 8_192, _QWEN),
    ("qwen", "qwen3-max"): _capacity(262_144, 65_536, _QWEN),
    # The moving qwen-plus alias has region-specific limits.  Bind only the
    # documented cross-deployment lower bound rather than overstate capacity.
    ("qwen", "qwen-plus"): _capacity(131_072, 8_192, _QWEN),
    ("qwen", "qwen-flash"): _capacity(1_000_000, 32_768, _QWEN),
    **{
        ("qwen", model): _capacity(1_000_000, 65_536, _QWEN)
        for model in (
            "qwen3.7-max",
            "qwen3.7-max-us",
            "qwen3.7-plus",
            "qwen3.7-plus-us",
            "qwen3.7-flash",
            "qwen3.8-max",
            "qwen3.8-flash",
        )
    },
}

_PROVIDER_MODEL_FAMILY = {
    # These CLIs expose an exact upstream model ID when the author selects one.
    # Sentinel IDs such as ``codex-cli`` or ``claude-code`` are not mapped.
    "codex_cli": "openai",
    "claude_cli": "anthropic",
}

_OFFICIAL_API_HOSTS: dict[str, frozenset[str]] = {
    "openai": frozenset({"api.openai.com"}),
    "anthropic": frozenset({"api.anthropic.com"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "gemini": frozenset({"generativelanguage.googleapis.com"}),
    "qwen": frozenset(
        {
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "dashscope-eu.aliyuncs.com",
        }
    ),
}


def known_model_capacity(
    provider: str | None,
    model_name: str | None,
) -> KnownModelCapacity | None:
    provider_key, model_key = canonical_model_identity(provider, model_name)
    provider_key = _PROVIDER_MODEL_FAMILY.get(provider_key, provider_key)
    return _CAPACITIES.get((provider_key, model_key))


def uses_documented_model_catalog(
    provider: str | None,
    base_url_override: str | None,
) -> bool:
    """Return whether an exact model ID is routed to its documented provider.

    Built-in API providers use their first-party endpoint when no override is
    configured.  Once an endpoint is overridden, both HTTPS and the exact
    first-party hostname must still match.  Exact upstream IDs exposed by the
    vendor CLIs are already provider-bound and do not have an API URL here.
    """

    provider_key = canonical_provider(provider)
    if provider_key in _PROVIDER_MODEL_FAMILY:
        return True
    official_hosts = _OFFICIAL_API_HOSTS.get(provider_key)
    if official_hosts is None:
        return False
    override = str(base_url_override or "").strip()
    if not override:
        return True
    parsed = urlsplit(override)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and hostname in official_hosts


__all__ = [
    "KnownModelCapacity",
    "known_model_capacity",
    "uses_documented_model_catalog",
]
