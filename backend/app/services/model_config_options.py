"""Pure normalization for configured and discovered model options."""

from __future__ import annotations

from typing import Any

from ..ai.local_cli_adapter import DEFAULT_CLI_MODELS, is_local_cli_provider
from ..core.exceptions import ValidationError
from ..core.model_capacity_catalog import known_model_capacity

DEEPSEEK_SUPPORTED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
DEEPSEEK_MODEL_ALIASES = {"deepseek-v3": "deepseek-v4-flash"}
MAX_CONTEXT_WINDOW_TOKENS = 10_000_000
MAX_OUTPUT_TOKENS = 1_000_000
MAX_SAFETY_MARGIN_TOKENS = 100_000


def normalize_model_for_provider(
    provider: str,
    model: str,
    *,
    strict: bool = True,
) -> str:
    if is_local_cli_provider(provider):
        return model or DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    if provider == "local_llama_cpp":
        return model
    if provider == "gemini":
        return model.removeprefix("models/")
    if provider != "deepseek":
        return model
    normalized = DEEPSEEK_MODEL_ALIASES.get(model, model)
    if normalized not in DEEPSEEK_SUPPORTED_MODELS and strict:
        supported = ", ".join(sorted(DEEPSEEK_SUPPORTED_MODELS))
        raise ValidationError(f"DeepSeek currently supports: {supported}")
    return normalized


def _capacity_values(raw: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    try:
        window = raw.get("context_window_tokens")
        output = raw.get("max_output_tokens")
        margin = raw.get("safety_margin_tokens")
        return (
            int(window) if window is not None else None,
            int(output) if output is not None else None,
            int(margin) if margin is not None else None,
        )
    except (TypeError, ValueError):
        return None, None, None


def normalized_model_options(
    provider: str,
    models: list[Any] | None,
    *,
    default_model: str,
    use_documented_catalog: bool = True,
) -> list[dict[str, Any]]:
    """Deduplicate options while preserving exact capacity evidence."""

    by_id: dict[str, dict[str, Any]] = {}
    for raw in models or []:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        raw_capacity: dict[str, Any] = {}
        if isinstance(raw, str):
            model_id = display_name = raw
        elif isinstance(raw, dict):
            model_id = str(raw.get("id") or raw.get("model") or "")
            display_name = str(raw.get("display_name") or raw.get("name") or model_id)
            raw_capacity = raw
        else:
            continue
        model_id = normalize_model_for_provider(provider, model_id.strip(), strict=False)
        if not model_id:
            continue
        option: dict[str, Any] = {
            "id": model_id,
            "display_name": display_name.strip() or model_id,
        }
        catalog = known_model_capacity(provider, model_id) if use_documented_catalog else None
        window, output, margin = _capacity_values(raw_capacity)
        raw_source = str(raw_capacity.get("capacity_source") or "")
        capacity_source: str | None = None
        if not use_documented_catalog and "_model_docs_" in raw_source:
            window = output = margin = None
        if window is None and catalog is not None:
            window = catalog.context_window_tokens
            output = catalog.max_output_tokens
            margin = 512
            capacity_source = catalog.source
        elif window is not None:
            capacity_source = str(
                raw_capacity.get("capacity_source") or "provider_metadata"
            )[:100]
        effective_margin = 512 if margin is None else margin
        capacity_valid = (
            window is not None
            and 2048 <= window <= MAX_CONTEXT_WINDOW_TOKENS
            and 0 <= effective_margin <= MAX_SAFETY_MARGIN_TOKENS
            and (
                output is None
                or (
                    0 < output <= MAX_OUTPUT_TOKENS
                    and output + effective_margin < window
                )
            )
        )
        if capacity_valid and window is not None:
            option["capacity_source"] = capacity_source or "provider_metadata"
            option["context_window_tokens"] = window
            if output is not None and 0 < output < window:
                option["max_output_tokens"] = output
            option["safety_margin_tokens"] = effective_margin
        by_id[model_id] = option

    normalized_default = normalize_model_for_provider(
        provider,
        default_model,
        strict=False,
    )
    default_option = by_id.pop(normalized_default, None) or {
        "id": normalized_default,
        "display_name": normalized_default,
    }
    if "context_window_tokens" not in default_option and use_documented_catalog:
        catalog = known_model_capacity(provider, normalized_default)
        if catalog is not None:
            default_option.update(
                context_window_tokens=catalog.context_window_tokens,
                max_output_tokens=catalog.max_output_tokens,
                safety_margin_tokens=512,
                capacity_source=catalog.source,
            )
    return [default_option, *by_id.values()]


def enriched_model_options(
    provider: str,
    models: list[dict],
    *,
    use_documented_catalog: bool,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for model in models:
        model_id = str(model.get("id") or model.get("model") or "").strip()
        if model_id:
            enriched.append(
                normalized_model_options(
                    provider,
                    [model],
                    default_model=model_id,
                    use_documented_catalog=use_documented_catalog,
                )[0]
            )
    return enriched


__all__ = [
    "DEEPSEEK_MODEL_ALIASES",
    "DEEPSEEK_SUPPORTED_MODELS",
    "enriched_model_options",
    "normalize_model_for_provider",
    "normalized_model_options",
]
