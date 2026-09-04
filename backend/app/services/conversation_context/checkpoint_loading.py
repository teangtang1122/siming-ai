"""Strict checkpoint decoding, provenance validation, and stale recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .canonical import canonical_sha256, canonical_value
from .checkpoint_state import safe_public_error_detail
from .checkpoint_validator import CheckpointSourceMessage, validate_checkpoint
from .codec import checkpoint_from_dict
from .contracts import (
    AuthorQuote,
    ConversationKind,
    ConversationTurn,
    ExecutionLedgerEntry,
    GenerationModelBinding,
    SourceRange,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .runtime_types import (
    CONVERSATION_CONTEXT_POLICY_VERSION,
    ActiveCheckpoint,
    ConversationContextStore,
)
from .store_phases import commit_context_phase, refresh_context_phase
from .transcript import checkpoint_source_messages


def _strict_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            f"持久化 checkpoint 的 {field} 不是对象。",
        )
    return value


def _strict_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            f"持久化 checkpoint 的 {field} 不是数组。",
        )
    return value


def checkpoint_from_record(
    record: Any,
    *,
    conversation_kind: ConversationKind,
    conversation_id: str,
    allow_superseded: bool = False,
):
    """Strictly decode normalized checkpoint columns from persistence."""

    allowed_statuses = {"ready", "superseded"} if allow_superseded else {"ready"}
    if str(getattr(record, "status", "") or "") not in allowed_statuses:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            "active checkpoint 尚未处于 ready 状态。",
        )
    policy_version = int(getattr(record, "policy_version", 0) or 0)
    if policy_version != CONVERSATION_CONTEXT_POLICY_VERSION:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint policy 版本已变化，必须重新整理。",
            details={
                "persisted_policy_version": policy_version,
                "current_policy_version": CONVERSATION_CONTEXT_POLICY_VERSION,
            },
        )
    validation = _strict_mapping(getattr(record, "validation_json", None), "validation_json")
    if validation.get("schema") != "conversation_checkpoint_validation.v1":
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            "checkpoint 缺少受支持的确定性校验记录。",
        )
    if validation.get("scope") != conversation_kind.value:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint scope 与当前会话不一致。",
        )
    if validation.get("conversation_id") != conversation_id:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint conversation_id 与当前会话不一致。",
        )
    payload = {
        "schema": str(getattr(record, "schema_version", "") or ""),
        "scope": conversation_kind.value,
        "conversation_id": conversation_id,
        "source_range": {
            "first_sequence": int(getattr(record, "source_first_sequence", 0) or 0),
            "last_sequence": int(getattr(record, "source_last_sequence", 0) or 0),
            "message_count": int(getattr(record, "source_message_count", 0) or 0),
            "source_hash": str(getattr(record, "source_hash", "") or ""),
        },
        "semantic_navigation": dict(
            _strict_mapping(
                getattr(record, "semantic_navigation_json", None),
                "semantic_navigation_json",
            )
        ),
        "author_quotes": _strict_list(
            getattr(record, "author_quotes_json", None), "author_quotes_json"
        ),
        "execution_ledger": _strict_list(
            getattr(record, "execution_ledger_json", None), "execution_ledger_json"
        ),
        "project_refs": _strict_list(
            getattr(record, "project_refs_json", None), "project_refs_json"
        ),
        "warnings": _strict_list(validation.get("warnings"), "validation_json.warnings"),
        "segment_ids": _strict_list(validation.get("segment_ids"), "validation_json.segment_ids"),
        "policy_version": policy_version,
    }
    try:
        checkpoint = checkpoint_from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            "持久化 checkpoint 未通过严格 codec。",
        ) from exc
    if str(validation.get("checkpoint_fingerprint") or "") != checkpoint.fingerprint:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint fingerprint 不匹配。",
        )
    binding_payload = _strict_mapping(
        getattr(record, "model_binding_json", None), "model_binding_json"
    )
    try:
        persisted_binding = GenerationModelBinding(**binding_payload)
    except (TypeError, ValueError) as exc:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            "checkpoint model binding 无效。",
        ) from exc
    fingerprint = str(getattr(record, "model_binding_fingerprint", "") or "")
    if persisted_binding.fingerprint != fingerprint:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint model binding fingerprint 不匹配。",
        )
    return checkpoint


def _source_turns_for_range(
    turns: Sequence[ConversationTurn],
    source_range: SourceRange,
) -> tuple[ConversationTurn, ...]:
    selected = tuple(
        turn
        for turn in turns
        if turn.messages[0].sequence_no >= source_range.first_sequence
        and turn.messages[-1].sequence_no <= source_range.last_sequence
    )
    if not selected:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源消息已不存在。",
        )
    for turn in selected:
        if not turn.checkpoint_eligible:
            raise ConversationContextError(
                ConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint 来源回合不再是可整理的 completed 可见回合。",
                details={
                    "turn_id": turn.turn_id,
                    "status": turn.status.value,
                },
            )
    messages = checkpoint_source_messages(selected)
    if (
        messages[0].sequence_no != source_range.first_sequence
        or messages[-1].sequence_no != source_range.last_sequence
        or len(messages) != source_range.message_count
    ):
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源范围不再对应完整回合。",
        )
    return selected


def checkpoint_sources_payload(
    *,
    new_source_messages: Sequence[CheckpointSourceMessage],
    prior_checkpoint_id: str | None,
    prior_checkpoint_hash: str | None,
    conversation_kind: ConversationKind,
    execution_ledger: Sequence[ExecutionLedgerEntry],
    execution_source_hashes: Mapping[str, str],
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if prior_checkpoint_id and prior_checkpoint_hash:
        sources.append(
            {
                "source_kind": "prior_segment",
                "source_id": prior_checkpoint_id,
                "source_sequence": None,
                "source_hash": prior_checkpoint_hash,
            }
        )
    sources.extend(
        {
            "source_kind": "message",
            "source_id": message.message_id,
            "source_sequence": message.sequence_no,
            "source_hash": canonical_sha256(
                {
                    "message_id": message.message_id,
                    "sequence_no": message.sequence_no,
                    "role": message.role.value,
                    "content": message.content,
                    "status": message.status,
                }
            ),
        }
        for message in new_source_messages
    )
    if conversation_kind is ConversationKind.WORKSPACE:
        sources.extend(
            {
                "source_kind": "run_step",
                "source_id": entry.step_id,
                "source_sequence": None,
                "source_hash": execution_source_hashes.get(entry.step_id, canonical_sha256(entry)),
            }
            for entry in execution_ledger
        )
    else:
        by_run: dict[str, list[ExecutionLedgerEntry]] = {}
        for entry in execution_ledger:
            by_run.setdefault(entry.run_id, []).append(entry)
        sources.extend(
            {
                "source_kind": "run_step",
                "source_id": run_id,
                "source_sequence": None,
                "source_hash": canonical_sha256(
                    [
                        {
                            "entry": canonical_value(entry),
                            "source_hash": execution_source_hashes.get(entry.step_id),
                        }
                        for entry in entries
                    ]
                ),
            }
            for run_id, entries in sorted(by_run.items())
        )
    return sources


def _validate_persisted_sources(
    *,
    store: ConversationContextStore,
    conversation_kind: ConversationKind,
    conversation_id: str,
    owner_id: str,
    segment_id: str,
    source_messages: Sequence[CheckpointSourceMessage],
    prior_segment_id: str | None,
    prior_segment_hash: str | None,
    execution_ledger: Sequence[ExecutionLedgerEntry],
    execution_source_hashes: Mapping[str, str],
) -> None:
    expected_payload = checkpoint_sources_payload(
        new_source_messages=source_messages,
        prior_checkpoint_id=prior_segment_id,
        prior_checkpoint_hash=prior_segment_hash,
        conversation_kind=conversation_kind,
        execution_ledger=execution_ledger,
        execution_source_hashes=execution_source_hashes,
    )
    expected = {
        (str(item["source_kind"]), str(item["source_id"])): (
            item.get("source_sequence"),
            str(item["source_hash"]),
        )
        for item in expected_payload
    }
    actual: dict[tuple[str, str], tuple[int | None, str]] = {}
    persisted = store.context_checkpoint_sources(
        conversation_kind.value,
        conversation_id,
        segment_id,
        owner_id=owner_id,
    )
    for source in persisted:
        identity = (
            str(getattr(source, "source_kind", "") or ""),
            str(getattr(source, "source_id", "") or ""),
        )
        if identity in actual:
            raise ConversationContextError(
                ConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint 来源表包含重复引用。",
                details={"source_kind": identity[0], "source_id": identity[1]},
            )
        sequence = getattr(source, "source_sequence", None)
        actual[identity] = (
            int(sequence) if sequence is not None else None,
            str(getattr(source, "source_hash", "") or ""),
        )
    if actual != expected:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源表与消息、父 segment 或执行回执不一致。",
            details={
                "missing_sources": sorted(
                    f"{kind}:{source_id}" for kind, source_id in expected.keys() - actual.keys()
                ),
                "extra_sources": sorted(
                    f"{kind}:{source_id}" for kind, source_id in actual.keys() - expected.keys()
                ),
                "changed_sources": sorted(
                    f"{kind}:{source_id}"
                    for kind, source_id in expected.keys() & actual.keys()
                    if expected[(kind, source_id)] != actual[(kind, source_id)]
                ),
            },
        )


def _checkpoint_records(
    *,
    store: ConversationContextStore,
    conversation_kind: ConversationKind,
    conversation_id: str,
    owner_id: str,
    checkpoint_id: str,
    checkpoint,
    record: Any,
) -> list[tuple[str, Any, Any]]:
    records: list[tuple[str, Any, Any]] = []
    for segment_id in checkpoint.segment_ids:
        segment_record = store.context_checkpoint(
            conversation_kind.value,
            conversation_id,
            segment_id,
            owner_id=owner_id,
        )
        if segment_record is None:
            raise ConversationContextError(
                ConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint prior segment 不存在。",
                details={"segment_id": segment_id},
            )
        records.append(
            (
                segment_id,
                segment_record,
                checkpoint_from_record(
                    segment_record,
                    conversation_kind=conversation_kind,
                    conversation_id=conversation_id,
                    allow_superseded=True,
                ),
            )
        )
    records.append((checkpoint_id, record, checkpoint))
    return records


def load_active_checkpoint(
    *,
    store: ConversationContextStore,
    conversation,
    owner_id: str,
    turns: Sequence[ConversationTurn],
    trusted_execution_ledger: Sequence[ExecutionLedgerEntry] = (),
    execution_source_hashes: Mapping[str, str] | None = None,
) -> tuple[Any, ActiveCheckpoint | None]:
    """Load and prove the complete active aggregate before rendering it."""

    state = store.ensure_context_state(conversation.kind.value, conversation.id, owner_id=owner_id)
    if state is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "会话不存在或不属于当前 owner。",
        )
    checkpoint_id = str(getattr(state, "active_checkpoint_id", "") or "")
    if not checkpoint_id:
        return state, None
    record = store.context_checkpoint(
        conversation.kind.value, conversation.id, checkpoint_id, owner_id=owner_id
    )
    if record is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "active checkpoint 记录不存在。",
        )
    checkpoint = checkpoint_from_record(
        record,
        conversation_kind=conversation.kind,
        conversation_id=conversation.id,
    )
    records = _checkpoint_records(
        store=store,
        conversation_kind=conversation.kind,
        conversation_id=conversation.id,
        owner_id=owner_id,
        checkpoint_id=checkpoint_id,
        checkpoint=checkpoint,
        record=record,
    )
    trusted = {entry.step_id: entry for entry in trusted_execution_ledger}
    source_hashes = execution_source_hashes or {}
    trusted_quotes: dict[tuple[str, int, int], AuthorQuote] = {}
    covered_ranges: list[tuple[int, int]] = []
    seen_records: set[str] = set()
    prior_id: str | None = None
    prior_hash: str | None = None
    for segment_id, _, segment in records:
        if segment_id in seen_records:
            raise ConversationContextError(
                ConversationContextErrorCode.CHECKPOINT_FAILED,
                "checkpoint segment chain 包含重复引用。",
            )
        seen_records.add(segment_id)
        source_messages = checkpoint_source_messages(
            _source_turns_for_range(turns, segment.source_range)
        )
        # Provenance changes are staleness, even when the trusted active
        # ledger has already folded a retried/resolved step out. Validate the
        # immutable source rows first so an actual RunStep retry is rebuilt
        # instead of being misreported as a malformed checkpoint.
        _validate_persisted_sources(
            store=store,
            conversation_kind=conversation.kind,
            conversation_id=conversation.id,
            owner_id=owner_id,
            segment_id=segment_id,
            source_messages=source_messages,
            prior_segment_id=prior_id,
            prior_segment_hash=prior_hash,
            execution_ledger=segment.execution_ledger,
            execution_source_hashes=source_hashes,
        )
        validate_checkpoint(
            segment,
            source_messages=source_messages,
            expected_scope=conversation.kind,
            expected_conversation_id=conversation.id,
            trusted_execution_ledger=trusted,
            trusted_author_quotes=trusted_quotes,
        )
        trusted_quotes.update(
            {
                (quote.message_id, quote.start_char, quote.end_char): quote
                for quote in segment.author_quotes
            }
        )
        covered_ranges.append(
            (segment.source_range.first_sequence, segment.source_range.last_sequence)
        )
        prior_id, prior_hash = segment_id, segment.fingerprint
    ordered = sorted(covered_ranges)
    if ordered != covered_ranges or any(
        current[0] <= previous[1] for previous, current in zip(ordered, ordered[1:], strict=False)
    ):
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            "checkpoint segment ranges 必须按时间排序且互不重叠。",
        )
    return state, ActiveCheckpoint(
        checkpoint_id,
        checkpoint,
        record,
        tuple(item[2] for item in records),
        tuple(covered_ranges),
    )


def load_active_checkpoint_with_stale_recovery(
    *,
    store: ConversationContextStore,
    conversation,
    owner_id: str,
    turns: Sequence[ConversationTurn],
    trusted_execution_ledger: Sequence[ExecutionLedgerEntry],
    execution_source_hashes: Mapping[str, str],
) -> tuple[Any, ActiveCheckpoint | None]:
    """CAS-clear only the stale active pointer inspected by this request."""

    latest_error: ConversationContextError | None = None
    for _ in range(3):
        try:
            return load_active_checkpoint(
                store=store,
                conversation=conversation,
                owner_id=owner_id,
                turns=turns,
                trusted_execution_ledger=trusted_execution_ledger,
                execution_source_hashes=execution_source_hashes,
            )
        except ConversationContextError as exc:
            if exc.code is not ConversationContextErrorCode.SOURCE_CHANGED:
                raise
            latest_error = exc
        state = store.context_state(conversation.kind.value, conversation.id, owner_id=owner_id)
        checkpoint_id = str(getattr(state, "active_checkpoint_id", "") or "")
        if state is None or not checkpoint_id:
            raise latest_error
        invalidated = store.invalidate_active_context_checkpoint(
            conversation.kind.value,
            conversation.id,
            checkpoint_id,
            int(getattr(state, "revision", -1)),
            owner_id=owner_id,
            error_code=latest_error.code.value,
            error_detail=(
                safe_public_error_detail(latest_error.code)
                or "对话上下文来源已变化，需要重新整理。"
            ),
        )
        if invalidated:
            commit_context_phase(store)
        refresh_context_phase(store)
    raise ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
        "活动 checkpoint 在失效重建期间被并发替换，请按最新会话状态重试。",
    ) from latest_error


__all__ = [
    "checkpoint_from_record",
    "checkpoint_sources_payload",
    "load_active_checkpoint",
    "load_active_checkpoint_with_stale_recovery",
]
