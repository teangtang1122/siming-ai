"""Shared results returned by story application ports."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.content_sync import ContentSyncIntent


@dataclass(slots=True)
class StoryMutation:
    """A persistence result plus mirror work to commit in the same transaction."""

    data: Any = None
    sync_intents: list[ContentSyncIntent] = field(default_factory=list)


__all__ = ["StoryMutation"]
