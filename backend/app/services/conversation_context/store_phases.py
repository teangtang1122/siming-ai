"""Explicit transaction and observer boundaries for context state transitions."""

from __future__ import annotations

import inspect

from .runtime_types import ContextEventSink, ConversationContextStore


def commit_context_phase(store: ConversationContextStore) -> None:
    """Commit through the explicit store port; never introspect an ORM session."""

    store.commit_context_phase()


def refresh_context_phase(store: ConversationContextStore) -> None:
    """Reload owner/CAS state through the explicit store port."""

    store.refresh_context_phase()


async def emit_best_effort(
    sink: ContextEventSink | None,
    event: str,
    payload: dict[str, object],
) -> None:
    """Keep disconnected transport observers out of the durable state machine."""

    if sink is None:
        return
    try:
        result = sink(event, payload)
        if inspect.isawaitable(result):
            await result
    except Exception:
        return


__all__ = ["commit_context_phase", "emit_best_effort", "refresh_context_phase"]
