"""Validated capacity carried by one request-scoped provider override."""

from __future__ import annotations

from dataclasses import dataclass

from .request_override import active_request_provider


@dataclass(frozen=True)
class RequestModelCapacity:
    context_window_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    known: bool


def active_request_capacity(
    provider: str,
    model_name: str,
) -> RequestModelCapacity | None:
    """Return only an explicit, internally consistent provider/model profile."""

    config = active_request_provider()
    if (
        config is None
        or config.provider != provider
        or config.default_model != model_name
    ):
        return None
    window = int(config.context_window_tokens or 0)
    output = int(config.max_output_tokens or 0)
    margin = int(config.safety_margin_tokens or 0)
    if window <= 0 or output <= 0 or margin < 0 or output + margin >= window:
        return None
    return RequestModelCapacity(
        context_window_tokens=window,
        max_output_tokens=output,
        safety_margin_tokens=margin,
        known=config.capacity_assurance != "unverified",
    )


__all__ = ["RequestModelCapacity", "active_request_capacity"]
