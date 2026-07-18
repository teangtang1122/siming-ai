"""Request-local context binding shared by orchestration and model runtime."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol


class ContextManifestLike(Protocol):
    id: str
    rendered_context: str | None
    output_reserve_tokens: int


@dataclass(frozen=True)
class ActiveContextManifest:
    manifest_id: str
    rendered_context: str
    output_reserve_tokens: int


_ACTIVE_CONTEXT_MANIFEST: ContextVar[ActiveContextManifest | None] = ContextVar(
    "siming_active_context_manifest",
    default=None,
)


@contextmanager
def activate_context_manifest(
    manifest: ContextManifestLike,
) -> Iterator[ActiveContextManifest]:
    active = ActiveContextManifest(
        manifest_id=manifest.id,
        rendered_context=manifest.rendered_context or "",
        output_reserve_tokens=manifest.output_reserve_tokens,
    )
    token = _ACTIVE_CONTEXT_MANIFEST.set(active)
    try:
        yield active
    finally:
        _ACTIVE_CONTEXT_MANIFEST.reset(token)


def active_context_manifest() -> ActiveContextManifest | None:
    return _ACTIVE_CONTEXT_MANIFEST.get()
