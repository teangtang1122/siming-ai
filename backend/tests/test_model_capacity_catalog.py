"""Authoritative model-capacity catalog regression coverage."""

import pytest

from app.core.model_capacity_catalog import (
    known_model_capacity,
    uses_documented_model_catalog,
)


@pytest.mark.parametrize(
    ("provider", "model", "window", "output"),
    [
        ("openai", "gpt-4o", 128_000, 16_384),
        ("openai", "gpt-4o-2024-11-20", 128_000, 16_384),
        ("openai", "gpt-5.6-sol", 1_050_000, 128_000),
        ("openai", "gpt-5.4-mini", 400_000, 128_000),
        ("anthropic", "claude-opus-4-8", 1_000_000, 128_000),
        ("anthropic", "claude-3-5-sonnet-20241022", 200_000, 8_192),
        ("deepseek", "deepseek-v4-flash", 1_000_000, 384_000),
        ("deepseek", "deepseek-v3", 1_000_000, 384_000),
        ("gemini", "models/gemini-2.5-flash", 1_048_576, 65_536),
        ("gemini", "gemini-3.7-flash", 1_048_576, 65_536),
        ("qwen", "qwen-max", 32_768, 8_192),
        ("qwen", "qwen-plus", 131_072, 8_192),
        ("qwen", "qwen3.7-plus", 1_000_000, 65_536),
        ("qwen", "qwen3.8-max", 1_000_000, 65_536),
        ("codex_cli", "gpt-5.6-sol", 1_050_000, 128_000),
    ],
)
def test_exact_documented_models_have_verified_capacity(
    provider: str,
    model: str,
    window: int,
    output: int,
) -> None:
    capacity = known_model_capacity(provider, model)

    assert capacity is not None
    assert capacity.context_window_tokens == window
    assert capacity.max_output_tokens == output
    assert capacity.source.endswith("2026_08_30")


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-4o-custom"),
        ("codex_cli", "codex-cli"),
        ("claude_cli", "claude-code"),
        ("openrouter", "openai/gpt-4o"),
        ("custom_vendor", "gpt-4o"),
    ],
)
def test_aliases_and_custom_deployments_remain_unverified(
    provider: str,
    model: str,
) -> None:
    assert known_model_capacity(provider, model) is None


@pytest.mark.parametrize(
    ("provider", "base_url", "expected"),
    [
        ("openai", None, True),
        ("openai", "https://api.openai.com/v1", True),
        ("openai", "https://proxy.example/v1", False),
        ("openai", "http://api.openai.com/v1", False),
        ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", True),
        ("deepseek", "https://api.deepseek.com./v1", True),
        ("codex_cli", None, True),
        ("custom_vendor", "https://api.openai.com/v1", False),
    ],
)
def test_documented_catalog_requires_the_provider_endpoint(
    provider: str,
    base_url: str | None,
    expected: bool,
) -> None:
    assert uses_documented_model_catalog(provider, base_url) is expected
