"""Canonical provider/model identities shared by configuration and execution.

Only aliases that the corresponding first-party adapter deterministically
rewrites before making a request belong here.  Marketing names, moving API
aliases and custom OpenAI-compatible deployments must remain untouched.
"""

from __future__ import annotations

DEEPSEEK_SUPPORTED_MODELS = frozenset(
    {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }
)
DEEPSEEK_MODEL_ALIASES = {
    # Siming 3.3.10 and earlier could persist this application-owned alias.
    # The DeepSeek adapter has always sent it as deepseek-v4-flash.
    "deepseek-v3": "deepseek-v4-flash",
}


def canonical_provider(provider: str | None) -> str:
    return str(provider or "").strip().lower()


def canonical_model_name(provider: str | None, model_name: str | None) -> str:
    """Return the exact model ID that the provider adapter will send."""

    provider_key = canonical_provider(provider)
    model_key = str(model_name or "").strip()
    if provider_key == "gemini":
        return model_key.removeprefix("models/")
    if provider_key == "deepseek":
        return DEEPSEEK_MODEL_ALIASES.get(model_key, model_key)
    return model_key


def canonical_model_identity(
    provider: str | None,
    model_name: str | None,
) -> tuple[str, str]:
    provider_key = canonical_provider(provider)
    return provider_key, canonical_model_name(provider_key, model_name)


__all__ = [
    "DEEPSEEK_MODEL_ALIASES",
    "DEEPSEEK_SUPPORTED_MODELS",
    "canonical_model_identity",
    "canonical_model_name",
    "canonical_provider",
]
