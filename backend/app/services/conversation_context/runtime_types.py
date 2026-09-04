"""Shared ports and immutable DTOs for conversation-context runtime stages."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .budget import RequestBudgetEnvelope
from .context_frame import ContextFrame
from .contracts import (
    ConversationCheckpoint,
    ConversationTurn,
)
from .provider_renderer import RenderedContextRequest

CONVERSATION_CONTEXT_POLICY_VERSION = 1


class ConversationContextStore(Protocol):
    """Owner-aware durable port used by the shared context state machine."""

    def context_state(
        self, conversation_kind: str, conversation_id: str, *, owner_id: str
    ) -> Any | None: ...

    def ensure_context_state(
        self, conversation_kind: str, conversation_id: str, *, owner_id: str
    ) -> Any | None: ...

    def context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> Any | None: ...

    def context_checkpoints(
        self, conversation_kind: str, conversation_id: str, *, owner_id: str
    ) -> Sequence[Any]: ...

    def context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> Sequence[Any]: ...

    def create_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
        **values: Any,
    ) -> Any | None: ...

    def add_context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        sources: Sequence[dict[str, Any]],
        *,
        owner_id: str,
    ) -> Sequence[Any] | None: ...

    def update_context_checkpoint_status(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        new_status: str,
        *,
        owner_id: str,
        expected_statuses: Sequence[str] | None = None,
        **values: Any,
    ) -> Any | None: ...

    def invalidate_active_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        error_code: str,
        error_detail: str,
    ) -> bool: ...

    def publish_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        last_budget_json: dict[str, Any] | None = None,
    ) -> bool: ...

    def supersede_inactive_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        error_code: str,
        error_detail: str,
    ) -> bool: ...

    def commit_context_phase(self) -> None:
        """Commit one explicit context state-machine phase."""

    def refresh_context_phase(self) -> None:
        """Expire cached records before an owner/CAS reload."""


CheckpointCompletion = Callable[..., Awaitable[dict[str, Any]]]
TurnReloader = Callable[[], Sequence[ConversationTurn] | Awaitable[Sequence[ConversationTurn]]]
ContextEventSink = Callable[[str, dict[str, Any]], None | Awaitable[None]]


@dataclass(frozen=True)
class ActiveCheckpoint:
    checkpoint_id: str
    checkpoint: ConversationCheckpoint
    record: Any
    checkpoint_chain: tuple[ConversationCheckpoint, ...]
    covered_sequence_ranges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class AssembledContextStep:
    frame: ContextFrame
    rendered: RenderedContextRequest
    budget: RequestBudgetEnvelope
    checkpoint_turns: tuple[ConversationTurn, ...]

    @property
    def provider_messages(self) -> list[dict[str, Any]]:
        return self.rendered.provider_messages()


@dataclass(frozen=True)
class PreparedConversationContext:
    step: AssembledContextStep
    context_state: dict[str, Any]
    checkpoint: dict[str, Any] | None
    trigger: str

    @property
    def frame(self) -> ContextFrame:
        return self.step.frame

    @property
    def rendered(self) -> RenderedContextRequest:
        return self.step.rendered

    @property
    def budget(self) -> RequestBudgetEnvelope:
        return self.step.budget

    @property
    def provider_messages(self) -> list[dict[str, Any]]:
        return self.step.provider_messages


__all__ = [
    "ActiveCheckpoint",
    "AssembledContextStep",
    "CheckpointCompletion",
    "CONVERSATION_CONTEXT_POLICY_VERSION",
    "ContextEventSink",
    "ConversationContextStore",
    "PreparedConversationContext",
    "TurnReloader",
]
