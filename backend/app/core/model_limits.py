"""Model capability defaults and user-tunable safety limits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS = 256_000
DEFAULT_MODEL_OUTPUT_TOKEN_LIMIT = 16000
MAX_CONFIGURABLE_LIMIT = 1000000

MODEL_OUTPUT_TOKEN_LIMITS: dict[tuple[str, str], int] = {
    ("deepseek", "deepseek-v4-pro"): 384000,
    ("deepseek", "deepseek-v4-flash"): 384000,
    ("gemini", "gemini-3-pro-preview"): 65536,
    ("gemini", "gemini-3-flash-preview"): 65536,
    ("gemini", "gemini-2.5-pro"): 65536,
    ("gemini", "gemini-2.5-flash"): 65536,
    ("gemini", "gemini-2.5-flash-lite"): 65536,
}

PROVIDER_OUTPUT_TOKEN_LIMITS: dict[str, int] = {
    "deepseek": 384000,
    "gemini": 65536,
}


@dataclass(frozen=True)
class ModelSafetyLimits:
    max_output_tokens: int
    deconstruct_input_char_limit: int
    deconstruct_item_char_limit: int


def _valid_limit(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return min(limit, MAX_CONFIGURABLE_LIMIT)


def default_output_token_limit(provider: Optional[str], model: Optional[str]) -> int:
    provider_key = (provider or "").strip()
    model_key = (model or "").strip()
    if (provider_key, model_key) in MODEL_OUTPUT_TOKEN_LIMITS:
        return MODEL_OUTPUT_TOKEN_LIMITS[(provider_key, model_key)]
    if provider_key in PROVIDER_OUTPUT_TOKEN_LIMITS:
        return PROVIDER_OUTPUT_TOKEN_LIMITS[provider_key]
    return DEFAULT_MODEL_OUTPUT_TOKEN_LIMIT


def effective_model_limits(
    provider: Optional[str],
    model: Optional[str],
    max_output_tokens: Optional[int] = None,
    deconstruct_input_char_limit: Optional[int] = None,
    deconstruct_item_char_limit: Optional[int] = None,
) -> ModelSafetyLimits:
    output_limit = _valid_limit(max_output_tokens) or default_output_token_limit(provider, model)
    input_limit = _valid_limit(deconstruct_input_char_limit) or output_limit
    item_limit = _valid_limit(deconstruct_item_char_limit) or output_limit
    return ModelSafetyLimits(
        max_output_tokens=output_limit,
        deconstruct_input_char_limit=input_limit,
        deconstruct_item_char_limit=item_limit,
    )


def limits_payload(
    provider: Optional[str],
    model: Optional[str],
    max_output_tokens: Optional[int] = None,
    deconstruct_input_char_limit: Optional[int] = None,
    deconstruct_item_char_limit: Optional[int] = None,
) -> dict:
    limits = effective_model_limits(
        provider,
        model,
        max_output_tokens=max_output_tokens,
        deconstruct_input_char_limit=deconstruct_input_char_limit,
        deconstruct_item_char_limit=deconstruct_item_char_limit,
    )
    return {
        "max_output_tokens": _valid_limit(max_output_tokens),
        "effective_max_output_tokens": limits.max_output_tokens,
        "deconstruct_input_char_limit": _valid_limit(deconstruct_input_char_limit),
        "effective_deconstruct_input_char_limit": limits.deconstruct_input_char_limit,
        "deconstruct_item_char_limit": _valid_limit(deconstruct_item_char_limit),
        "effective_deconstruct_item_char_limit": limits.deconstruct_item_char_limit,
    }
