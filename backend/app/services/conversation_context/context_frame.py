"""Provider-neutral active conversation ContextFrame."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .budget import RequestBudgetEnvelope
from .canonical import canonical_sha256, canonical_value
from .contracts import (
    ConversationCheckpoint,
    ConversationIdentity,
    ConversationMessage,
    ConversationRole,
    ConversationTurn,
    GenerationModelBinding,
    SystemContract,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .tool_transactions import ToolExecutionReceipt, ToolTransaction


@dataclass(frozen=True)
class ContextFrameIntegrity:
    transcript_revision: int
    checkpoint_hash: str | None
    frame_hash: str | None = None

    def __post_init__(self) -> None:
        if self.transcript_revision < 0:
            raise ValueError("transcript_revision must not be negative")


@dataclass(frozen=True)
class ContextFrame:
    conversation: ConversationIdentity
    model_binding: GenerationModelBinding
    system_contract: SystemContract
    checkpoint: ConversationCheckpoint | None
    recent_turns: tuple[ConversationTurn, ...]
    current_user_message: ConversationMessage
    current_turn_ledger: tuple[ToolExecutionReceipt, ...]
    pending_tool_transactions: tuple[ToolTransaction, ...]
    budget: RequestBudgetEnvelope
    integrity: ContextFrameIntegrity
    checkpoint_segments: tuple[ConversationCheckpoint, ...] = ()
    schema: str = "conversation_context_frame.v1"

    def __post_init__(self) -> None:
        if self.schema != "conversation_context_frame.v1":
            raise ValueError("unsupported conversation context frame schema")
        segments = tuple(self.checkpoint_segments)
        if self.checkpoint is not None and not segments:
            segments = (self.checkpoint,)
        object.__setattr__(self, "checkpoint_segments", segments)
        if self.current_user_message.role is not ConversationRole.USER:
            raise ValueError("current_user_message must keep the native user role")
        if not self.current_user_message.content:
            raise ValueError("current_user_message must be preserved verbatim")
        if self.conversation.revision != self.integrity.transcript_revision:
            raise ValueError("frame transcript revision does not match conversation revision")
        if self.model_binding.fingerprint != self.budget.model_binding_fingerprint:
            raise ValueError("frame budget is bound to a different model configuration")
        if self.checkpoint is not None:
            if self.checkpoint.scope is not self.conversation.kind:
                raise ValueError("checkpoint scope does not match conversation kind")
            if self.checkpoint.conversation_id != self.conversation.id:
                raise ValueError("checkpoint belongs to a different conversation")
            if self.integrity.checkpoint_hash != self.checkpoint.fingerprint:
                raise ValueError("checkpoint integrity hash mismatch")
            if self.checkpoint.source_range.last_sequence >= self.current_user_message.sequence_no:
                raise ValueError("checkpoint source must precede the current user message")
            if not segments or segments[-1].fingerprint != self.checkpoint.fingerprint:
                raise ValueError("active checkpoint must be the last checkpoint segment")
            previous_segment_last = -1
            for segment in segments:
                if segment.scope is not self.conversation.kind:
                    raise ValueError("checkpoint segment scope does not match conversation")
                if segment.conversation_id != self.conversation.id:
                    raise ValueError("checkpoint segment belongs to another conversation")
                if segment.source_range.first_sequence <= previous_segment_last:
                    raise ValueError("checkpoint segment ranges must be ordered and disjoint")
                if segment.source_range.last_sequence >= self.current_user_message.sequence_no:
                    raise ValueError("checkpoint segment source must precede current user")
                previous_segment_last = segment.source_range.last_sequence
        elif self.integrity.checkpoint_hash is not None:
            raise ValueError("checkpoint_hash must be null when checkpoint is absent")
        elif segments:
            raise ValueError("checkpoint_segments require an active checkpoint")

        previous_sequence = -1
        seen_message_ids: set[str] = set()
        for turn in self.recent_turns:
            if not turn.closed:
                raise ValueError("recent_turns may only contain closed turns")
            if not turn.safe_visible_projection:
                raise ConversationContextError(
                    ConversationContextErrorCode.PROTOCOL_INVALID,
                    "recent_turns 只能包含逐字 user 与最终 assistant；原生工具协议必须单独保存。",
                    details={"turn_id": turn.turn_id},
                )
            turn_first = turn.messages[0].sequence_no
            turn_last = turn.messages[-1].sequence_no
            if any(
                turn_first <= segment.source_range.last_sequence
                and segment.source_range.first_sequence <= turn_last
                for segment in segments
            ):
                raise ValueError("recent turn overlaps a checkpoint segment")
            for message in turn.messages:
                if message.sequence_no <= previous_sequence:
                    raise ValueError("recent turn messages must be globally ordered")
                if message.message_id in seen_message_ids:
                    raise ValueError("recent turn message IDs must be unique")
                if message.sequence_no >= self.current_user_message.sequence_no:
                    raise ValueError("recent turns must precede the current user message")
                previous_sequence = message.sequence_no
                seen_message_ids.add(message.message_id)
        if self.current_user_message.message_id in seen_message_ids:
            raise ValueError("current user message cannot also appear in recent turns")
        transaction_ids = [item.transaction_id for item in self.pending_tool_transactions]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("pending tool transaction IDs must be unique")

        expected_hash = self.calculate_hash()
        if self.integrity.frame_hash is not None and self.integrity.frame_hash != expected_hash:
            raise ValueError("context frame integrity hash mismatch")

    def to_dict(self, *, seal: bool = True) -> dict[str, Any]:
        payload = canonical_value(self)
        if seal:
            payload["integrity"]["frame_hash"] = self.calculate_hash()
        return payload

    def calculate_hash(self) -> str:
        payload = canonical_value(self)
        payload["integrity"]["frame_hash"] = None
        return canonical_sha256(payload)

    def sealed(self) -> ContextFrame:
        return replace(
            self,
            integrity=replace(self.integrity, frame_hash=self.calculate_hash()),
        )


__all__ = ["ContextFrame", "ContextFrameIntegrity"]
