"""Persistence boundary for author-confirmed model context profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..core.exceptions import ValidationError
from ..core.model_capacity_catalog import (
    known_model_capacity,
    uses_documented_model_catalog,
)
from ..core.model_limits import default_output_token_limit
from ..core.provider_model_identity import canonical_model_identity, canonical_model_name
from ..modules.context.infrastructure.models import ModelContextProfile


@dataclass(frozen=True)
class VerifiedModelCapacity:
    context_window_tokens: int
    max_output_tokens: int | None
    safety_margin_tokens: int


def local_context_task_type(task_type: str | None) -> str:
    """Map public task names to managed-runtime capacity profiles."""

    return {
        "new_project": "planning",
        "review": "evaluation",
        "rewrite": "writing",
        "outline_planning": "planning",
    }.get(str(task_type or ""), str(task_type or "chat"))


def _provider_metadata_capacity(
    provider_config: Any,
    model_name: str,
) -> VerifiedModelCapacity | None:
    provider, normalized_model = canonical_model_identity(
        getattr(provider_config, "provider", ""), model_name
    )
    for option in list(getattr(provider_config, "available_models_json", None) or []):
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or option.get("model") or "").strip()
        option_id = canonical_model_name(provider, option_id)
        if option_id != normalized_model:
            continue
        try:
            window = int(option.get("context_window_tokens") or 0)
            output = int(option.get("max_output_tokens") or 0)
            margin = int(option.get("safety_margin_tokens") or 512)
        except (TypeError, ValueError):
            return None
        if (
            not 2048 <= window <= 10_000_000
            or not 0 <= margin <= 100_000
            or output < 0
            or output > 1_000_000
            or (output > 0 and output + margin >= window)
        ):
            return None
        return VerifiedModelCapacity(window, output or None, margin)
    return None


def resolved_cloud_model_capacity(
    provider_config: Any | None,
    *,
    provider: str,
    model_name: str,
    configured_output_limit: int | None,
) -> VerifiedModelCapacity | None:
    """Resolve endpoint metadata before exact first-party documentation."""

    capacity = (
        _provider_metadata_capacity(provider_config, model_name)
        if provider_config is not None
        else None
    )
    configured_base_url = (
        getattr(provider_config, "base_url_override", None) if provider_config is not None else None
    )
    if configured_base_url is None and provider_config is not None:
        configured_base_url = getattr(provider_config, "base_url", None)
    if capacity is None and uses_documented_model_catalog(
        provider,
        configured_base_url,
    ):
        catalog = known_model_capacity(provider, model_name)
        if catalog is not None:
            capacity = VerifiedModelCapacity(
                catalog.context_window_tokens,
                catalog.max_output_tokens,
                512,
            )
    if capacity is None:
        return capacity
    output_limits = [capacity.context_window_tokens - capacity.safety_margin_tokens - 1]
    if capacity.max_output_tokens is not None:
        output_limits.append(capacity.max_output_tokens)
    if configured_output_limit is not None:
        output_limits.append(configured_output_limit)
    return VerifiedModelCapacity(
        capacity.context_window_tokens,
        min(output_limits),
        capacity.safety_margin_tokens,
    )


def configured_model_context_profile(
    db: Session,
    *,
    provider: str,
    model_name: str,
) -> ModelContextProfile | None:
    """Return the enabled author-confirmed profile for one exact model."""

    provider, model_name = canonical_model_identity(provider, model_name)
    return (
        db.query(ModelContextProfile)
        .filter(
            ModelContextProfile.provider == provider,
            ModelContextProfile.model_name == model_name,
            ModelContextProfile.enabled.is_(True),
        )
        .first()
    )


def save_model_context_profile(
    db: Session,
    *,
    provider: str,
    model_name: str,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    safety_margin_tokens: int | None,
) -> ModelContextProfile | None:
    """Validate and upsert one author-confirmed capacity profile."""

    provider, model_name = canonical_model_identity(provider, model_name)
    if context_window_tokens is None:
        return None
    window = int(context_window_tokens)
    margin = int(safety_margin_tokens or 0)
    output = int(
        max_output_tokens or default_output_token_limit(provider, model_name)
    )
    if output + margin >= window:
        raise ValidationError("模型最大输出与保护余量之和必须小于上下文窗口")
    profile = (
        db.query(ModelContextProfile)
        .filter(
            ModelContextProfile.provider == provider,
            ModelContextProfile.model_name == model_name,
        )
        .first()
    )
    if profile is None:
        profile = ModelContextProfile(provider=provider, model_name=model_name)
        db.add(profile)
    profile.context_window_tokens = window
    profile.max_output_tokens = output
    profile.safety_margin_tokens = margin
    profile.enabled = True
    return profile


__all__ = [
    "VerifiedModelCapacity",
    "configured_model_context_profile",
    "local_context_task_type",
    "resolved_cloud_model_capacity",
    "save_model_context_profile",
]
