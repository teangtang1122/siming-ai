"""Deterministic validation for model-produced checkpoint navigation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .canonical import canonical_sha256, text_sha256
from .contracts import (
    AuthorQuote,
    ConversationCheckpoint,
    ConversationKind,
    ConversationRole,
    ExecutionLedgerEntry,
)
from .errors import ConversationContextError, ConversationContextErrorCode


@dataclass(frozen=True)
class CheckpointSourceMessage:
    message_id: str
    sequence_no: int
    role: ConversationRole
    content: str
    status: str = "completed"

    def __post_init__(self) -> None:
        if not self.message_id or self.sequence_no <= 0:
            raise ValueError("checkpoint source message identity is invalid")


def checkpoint_source_hash(messages: Sequence[CheckpointSourceMessage]) -> str:
    """Hash a chronological immutable source bundle, including full content."""

    ordered = sorted(messages, key=lambda item: item.sequence_no)
    payload = [
        {
            "message_id": item.message_id,
            "sequence_no": item.sequence_no,
            "role": item.role.value,
            "content": item.content,
            "status": item.status,
        }
        for item in ordered
    ]
    return canonical_sha256(payload)


def validate_checkpoint(
    checkpoint: ConversationCheckpoint,
    *,
    source_messages: Sequence[CheckpointSourceMessage],
    expected_scope: ConversationKind,
    expected_conversation_id: str,
    trusted_execution_ledger: Mapping[str, ExecutionLedgerEntry] | None = None,
    trusted_author_quotes: Mapping[tuple[str, int, int], AuthorQuote] | None = None,
) -> None:
    """Validate all authoritative portions before a checkpoint is published."""

    if checkpoint.scope is not expected_scope:
        _source_changed("checkpoint scope does not match conversation")
    if checkpoint.conversation_id != expected_conversation_id:
        _source_changed("checkpoint belongs to another conversation")
    if not source_messages:
        _source_changed("checkpoint source bundle is empty")

    ordered = sorted(source_messages, key=lambda item: item.sequence_no)
    sequences = [item.sequence_no for item in ordered]
    if len(sequences) != len(set(sequences)):
        _source_changed("checkpoint source sequence contains duplicates")
    if any(right != left + 1 for left, right in zip(sequences, sequences[1:], strict=False)):
        _source_changed("checkpoint source range is not contiguous")
    if any(item.status != "completed" for item in ordered):
        _source_changed("only completed source messages may enter a checkpoint")

    source_range = checkpoint.source_range
    if (
        source_range.first_sequence != sequences[0]
        or source_range.last_sequence != sequences[-1]
        or source_range.message_count != len(ordered)
        or source_range.source_hash != checkpoint_source_hash(ordered)
    ):
        _source_changed("checkpoint source range or hash changed")

    by_id = {item.message_id: item for item in ordered}
    prior_quotes = trusted_author_quotes or {}
    seen_quote_keys: set[tuple[str, int, int]] = set()
    for quote in checkpoint.author_quotes:
        message = by_id.get(quote.message_id)
        key = (quote.message_id, quote.start_char, quote.end_char)
        if key in seen_quote_keys:
            _checkpoint_failed("duplicate author quote range")
        seen_quote_keys.add(key)
        if message is None:
            prior = prior_quotes.get(key)
            if prior is None or (
                prior.message_id != quote.message_id
                or prior.start_char != quote.start_char
                or prior.end_char != quote.end_char
                or prior.exact_quote != quote.exact_quote
                or prior.quote_sha256 != quote.quote_sha256
                or prior.purpose != quote.purpose
            ):
                _checkpoint_failed(
                    "author quote must reference this segment or a validated prior segment"
                )
            if prior.superseded and not quote.superseded:
                _checkpoint_failed("superseded author quote cannot become active again")
            continue
        if message.role is not ConversationRole.USER:
            _checkpoint_failed("author quote must reference a source user message")
        if quote.end_char > len(message.content):
            _checkpoint_failed("author quote range exceeds source message")
        exact = message.content[quote.start_char : quote.end_char]
        if exact != quote.exact_quote or text_sha256(exact) != quote.quote_sha256:
            _checkpoint_failed("author quote content, position, or hash is invalid")
        if quote.superseded:
            _checkpoint_failed("new source author quote cannot start as superseded")

    trusted = trusted_execution_ledger or {}
    seen_steps: set[str] = set()
    for entry in checkpoint.execution_ledger:
        if entry.step_id in seen_steps:
            _checkpoint_failed("duplicate execution ledger step_id")
        seen_steps.add(entry.step_id)
        authoritative = trusted.get(entry.step_id)
        if authoritative is None or authoritative != entry:
            _checkpoint_failed("execution ledger is not backed by the trusted RunStep receipt")

    project_ref_keys = [(item.type, item.id) for item in checkpoint.project_refs]
    if len(project_ref_keys) != len(set(project_ref_keys)):
        _checkpoint_failed("checkpoint project references must be deduplicated")


def _source_changed(message: str) -> None:
    raise ConversationContextError(
        ConversationContextErrorCode.SOURCE_CHANGED,
        message,
    )


def _checkpoint_failed(message: str) -> None:
    raise ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_FAILED,
        message,
    )


__all__ = [
    "CheckpointSourceMessage",
    "checkpoint_source_hash",
    "validate_checkpoint",
]
