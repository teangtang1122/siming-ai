"""Durable, transaction-phased generation of one checkpoint segment."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai.local_cli_adapter import is_local_cli_provider

from .assembly import _turn_cost
from .budget import TokenCounter
from .canonical import canonical_sha256, canonical_value
from .checkpoint_loading import (
    _validate_persisted_sources,
    checkpoint_from_record,
    checkpoint_sources_payload,
)
from .checkpoint_prompt import (
    build_checkpoint_messages,
    build_checkpoint_repair_messages,
    checkpoint_navigation_json_schema,
    materialize_author_quotes,
    parse_checkpoint_navigation,
    rollup_author_quotes,
)
from .checkpoint_renderer import render_checkpoint_reference
from .checkpoint_state import (
    checkpoint_record_payload,
    context_state_payload,
    mark_task_cancelled_checkpoint,
    publish_or_resolve_checkpoint_race,
    safe_public_error_detail,
)
from .checkpoint_validator import CheckpointSourceMessage, validate_checkpoint
from .contracts import (
    ConversationCheckpoint,
    ConversationIdentity,
    ConversationMessage,
    ConversationTurn,
    ExecutionLedgerEntry,
    GenerationModelBinding,
    SourceRange,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .execution_ledger import fold_execution_ledger, project_references_from_execution_ledger
from .runtime_types import (
    CONVERSATION_CONTEXT_POLICY_VERSION,
    ActiveCheckpoint,
    CheckpointCompletion,
    ContextEventSink,
    ConversationContextStore,
    TurnReloader,
)
from .store_phases import commit_context_phase, emit_best_effort, refresh_context_phase
from .transcript import (
    checkpoint_source_messages,
    source_range_for_turns,
    validate_transcript_snapshot,
)

_CHECKPOINT_OUTPUT_RESERVE = 4_096


def _default_checkpoint_completion() -> CheckpointCompletion:
    from app.modules.model_runtime.application.execution import model_executor

    return model_executor.chat_completion


def _checkpoint_completion_body() -> dict[str, Any]:
    return {
        "moshu_context_manifest_disabled": True,
        "response_format": {
            "type": "json_schema",
            "json_schema": checkpoint_navigation_json_schema(),
        },
    }


def _checkpoint_prompt_fits(
    *,
    messages: Sequence[Mapping[str, Any]],
    binding: GenerationModelBinding,
    counter: TokenCounter,
    safety_margin_tokens: int,
) -> bool:
    output = min(
        _CHECKPOINT_OUTPUT_RESERVE,
        max(1, binding.max_output_tokens or _CHECKPOINT_OUTPUT_RESERVE),
    )
    body = _checkpoint_completion_body()
    rendered_request = {
        "messages": list(messages),
        "tools": [],
        "tool_choice": "none",
        "temperature": 0,
        "max_tokens": output,
        "response_format": body.get("response_format"),
        "provider_wrapper": {key: value for key, value in body.items() if key != "response_format"},
    }
    return counter.count_value(rendered_request) <= max(
        0, binding.context_window_tokens - output - safety_margin_tokens
    )


def _require_prior_quote_rollup_capacity(
    *,
    active: ActiveCheckpoint | None,
    binding: GenerationModelBinding,
    counter: TokenCounter,
) -> None:
    active_quotes = tuple(
        quote
        for quote in (active.checkpoint.author_quotes if active is not None else ())
        if not quote.superseded
    )
    required_output = {
        "schema": "conversation_checkpoint_navigation.v1",
        "semantic_navigation": {
            "authority": "non_authoritative_navigation",
            "current_objectives": [],
            "resolved_decisions": [],
            "superseded_directions": [],
            "unresolved_questions": [],
            "next_context_needed": [],
        },
        "author_quote_positions": [],
        "prior_author_quote_states": [
            {
                "message_id": quote.message_id,
                "start_char": quote.start_char,
                "end_char": quote.end_char,
                "quote_sha256": quote.quote_sha256,
                "status": "active",
            }
            for quote in active_quotes
        ],
    }
    required_tokens = counter.count_value(required_output)
    output_limit = min(
        _CHECKPOINT_OUTPUT_RESERVE,
        max(1, binding.max_output_tokens or _CHECKPOINT_OUTPUT_RESERVE),
    )
    if required_tokens > output_limit:
        raise ConversationContextError(
            ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
            "仍有效的作者原话状态无法完整放入 checkpoint 模型输出预算。",
            details={
                "active_author_quote_count": len(active_quotes),
                "required_output_tokens": required_tokens,
                "checkpoint_output_limit": output_limit,
            },
        )


def _checkpoint_segment_that_fits(
    *,
    desired_turns: Sequence[ConversationTurn],
    active: ActiveCheckpoint | None,
    conversation: ConversationIdentity,
    binding: GenerationModelBinding,
    counter: TokenCounter,
    safety_margin_tokens: int,
    execution_ledger: Sequence[ExecutionLedgerEntry] = (),
) -> tuple[tuple[ConversationTurn, ...], list[dict[str, str]]]:
    _require_prior_quote_rollup_capacity(active=active, binding=binding, counter=counter)
    selected: list[ConversationTurn] = []
    selected_messages: list[dict[str, str]] | None = None
    for turn in desired_turns:
        candidate = (*selected, turn)
        messages = build_checkpoint_messages(
            scope=conversation.kind.value,
            conversation_id=conversation.id,
            source_messages=checkpoint_source_messages(candidate),
            previous_navigation=(
                active.checkpoint.semantic_navigation if active is not None else None
            ),
            previous_author_quotes=(active.checkpoint.author_quotes if active is not None else ()),
            execution_ledger=execution_ledger,
        )
        if not _checkpoint_prompt_fits(
            messages=messages,
            binding=binding,
            counter=counter,
            safety_margin_tokens=safety_margin_tokens,
        ):
            break
        selected.append(turn)
        selected_messages = messages
    if not selected or selected_messages is None:
        raise ConversationContextError(
            ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
            "最早一个待整理完整回合自身超过 checkpoint 模型容量。",
            details={
                "turn_id": desired_turns[0].turn_id if desired_turns else None,
                "context_window_tokens": binding.context_window_tokens,
            },
        )
    return tuple(selected), selected_messages


async def _call_checkpoint_model(
    *,
    completion: CheckpointCompletion,
    messages: list[dict[str, str]],
    binding: GenerationModelBinding,
    counter: TokenCounter,
    safety_margin_tokens: int,
) -> Any:
    if is_local_cli_provider(binding.provider):
        # ``tools=[]`` controls an API request, but cannot remove the shell,
        # filesystem, global MCP, rules, or ambient configuration of a local
        # Agent process.  Empty cwd and best-effort environment flags are not
        # a hard security boundary against prompt injection in old history.
        # Until every provider has a proven OS/CLI-level no-tool sandbox, no
        # local Agent CLI may act as the checkpoint summarizer.  Keep the same
        # model binding and fail explicitly; never downgrade to another
        # provider behind the author's back.
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            (
                "本机 Agent CLI 尚不能证明 checkpoint 所需的无工具、无文件、无 MCP "
                "硬隔离，本次任务未执行；完整对话仍已保留。"
                "请切换已验证的 API 模型后重试，或新建对话继续。"
            ),
        )
    output = min(
        _CHECKPOINT_OUTPUT_RESERVE,
        max(1, binding.max_output_tokens or _CHECKPOINT_OUTPUT_RESERVE),
    )
    body = _checkpoint_completion_body()

    async def call(request_messages: list[dict[str, str]]) -> dict[str, Any]:
        if not _checkpoint_prompt_fits(
            messages=request_messages,
            binding=binding,
            counter=counter,
            safety_margin_tokens=safety_margin_tokens,
        ):
            raise ConversationContextError(
                ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
                "checkpoint 结构修复请求超过模型容量。",
            )
        result = await completion(
            messages=request_messages,
            model=binding.normalized_model,
            temperature=0,
            max_tokens=output,
            extra_body=body,
            tools=[],
            tool_choice="none",
        )
        if result.get("tool_calls"):
            raise ConversationContextError(
                ConversationContextErrorCode.CHECKPOINT_FAILED,
                "checkpoint 模型不得返回工具调用。",
            )
        return result

    first = await call(messages)
    raw = str(first.get("content") or "")
    try:
        return parse_checkpoint_navigation(raw)
    except ConversationContextError as first_error:
        repair = build_checkpoint_repair_messages(
            original_messages=messages,
            invalid_output=raw,
            validation_error=str(first_error),
        )
        second = await call(repair)
        return parse_checkpoint_navigation(str(second.get("content") or ""))


@dataclass(frozen=True)
class _GenerationRequest:
    store: ConversationContextStore
    conversation: ConversationIdentity
    owner_id: str
    all_turns: tuple[ConversationTurn, ...]
    desired_turns: tuple[ConversationTurn, ...]
    active: ActiveCheckpoint | None
    state: Any
    binding: GenerationModelBinding
    counter: TokenCounter
    safety_margin_tokens: int
    trusted_execution_ledger: tuple[ExecutionLedgerEntry, ...]
    execution_source_hashes: Mapping[str, str]
    reload_turns: TurnReloader
    completion: CheckpointCompletion
    event_sink: ContextEventSink | None


@dataclass(frozen=True)
class _GenerationPlan:
    folded_ledger: tuple[ExecutionLedgerEntry, ...]
    segment_turns: tuple[ConversationTurn, ...]
    checkpoint_messages: list[dict[str, str]]
    segment_range: SourceRange
    source_messages: tuple[CheckpointSourceMessage, ...]
    idempotency_key: str
    expected_revision: int


@dataclass(frozen=True)
class _Attempt:
    record_id: str
    expected_revision: int


def _merge_segment_ids(active: ActiveCheckpoint | None) -> tuple[str, ...]:
    if active is None:
        return ()
    return tuple(dict.fromkeys((*active.checkpoint.segment_ids, active.checkpoint_id)))


def _plan_generation(request: _GenerationRequest) -> _GenerationPlan:
    folded = fold_execution_ledger(request.trusted_execution_ledger)
    segment, messages = _checkpoint_segment_that_fits(
        desired_turns=request.desired_turns,
        active=request.active,
        conversation=request.conversation,
        binding=request.binding,
        counter=request.counter,
        safety_margin_tokens=request.safety_margin_tokens,
        execution_ledger=folded,
    )
    source_range = source_range_for_turns(segment)
    source_messages = checkpoint_source_messages(segment)
    provenance_hash = canonical_sha256(
        [
            {
                "entry": canonical_value(entry),
                "source_hash": request.execution_source_hashes.get(
                    entry.step_id, canonical_sha256(entry)
                ),
            }
            for entry in folded
        ]
    )
    active = request.active
    key = canonical_sha256(
        {
            "schema": "conversation_checkpoint_attempt.v1",
            "policy_version": CONVERSATION_CONTEXT_POLICY_VERSION,
            "scope": request.conversation.kind.value,
            "conversation_id": request.conversation.id,
            # Context revision changes only when a derived checkpoint is
            # published/invalidated.  Range-external transcript appends do not
            # change it, so concurrent users reuse the same immutable attempt;
            # a deliberately invalidated checkpoint can still be rebuilt.
            "context_revision": int(getattr(request.state, "revision", 0) or 0),
            "source_range": canonical_value(source_range),
            "execution_provenance_hash": provenance_hash,
            "parent_checkpoint_id": active.checkpoint_id if active else None,
            "parent_checkpoint_hash": active.checkpoint.fingerprint if active else None,
        }
    )
    return _GenerationPlan(
        tuple(folded),
        segment,
        messages,
        source_range,
        tuple(source_messages),
        key,
        int(getattr(request.state, "revision", 0) or 0),
    )


def _active_result(
    request: _GenerationRequest,
    plan: _GenerationPlan,
    *,
    record: Any,
    checkpoint: ConversationCheckpoint,
) -> ActiveCheckpoint:
    active = request.active
    return ActiveCheckpoint(
        str(getattr(record, "id", "") or ""),
        checkpoint,
        record,
        (*((active.checkpoint_chain) if active else ()), checkpoint),
        (
            *((active.covered_sequence_ranges) if active else ()),
            (plan.segment_range.first_sequence, plan.segment_range.last_sequence),
        ),
    )


def _validate_ready_attempt(
    request: _GenerationRequest,
    plan: _GenerationPlan,
    record: Any,
) -> ActiveCheckpoint:
    checkpoint = checkpoint_from_record(
        record,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
    )
    active = request.active
    validate_checkpoint(
        checkpoint,
        source_messages=plan.source_messages,
        expected_scope=request.conversation.kind,
        expected_conversation_id=request.conversation.id,
        trusted_execution_ledger={
            entry.step_id: entry for entry in request.trusted_execution_ledger
        },
        trusted_author_quotes={
            (quote.message_id, quote.start_char, quote.end_char): quote
            for quote in (active.checkpoint.author_quotes if active else ())
        },
    )
    _validate_persisted_sources(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        segment_id=str(getattr(record, "id", "") or ""),
        source_messages=plan.source_messages,
        prior_segment_id=active.checkpoint_id if active else None,
        prior_segment_hash=active.checkpoint.fingerprint if active else None,
        execution_ledger=plan.folded_ledger,
        execution_source_hashes=request.execution_source_hashes,
    )
    publish_or_resolve_checkpoint_race(
        store=request.store,
        conversation=request.conversation,
        owner_id=request.owner_id,
        checkpoint_id=str(getattr(record, "id", "") or ""),
        expected_revision=plan.expected_revision,
    )
    commit_context_phase(request.store)
    return _active_result(request, plan, record=record, checkpoint=checkpoint)


def _create_attempt(
    request: _GenerationRequest,
    plan: _GenerationPlan,
) -> ActiveCheckpoint | _Attempt:
    active = request.active
    record = request.store.create_context_checkpoint(
        request.conversation.kind.value,
        request.conversation.id,
        owner_id=request.owner_id,
        idempotency_key=plan.idempotency_key,
        parent_checkpoint_id=active.checkpoint_id if active else None,
        policy_version=CONVERSATION_CONTEXT_POLICY_VERSION,
        schema_version="conversation_checkpoint.v1",
        status="pending",
        source_first_sequence=plan.segment_range.first_sequence,
        source_last_sequence=plan.segment_range.last_sequence,
        source_message_count=plan.segment_range.message_count,
        source_hash=plan.segment_range.source_hash,
        transcript_revision=request.conversation.revision,
        model_binding_json=request.binding.to_dict(),
        model_binding_fingerprint=request.binding.fingerprint,
    )
    if record is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "无法为当前 owner 创建 checkpoint attempt。",
        )
    status = str(getattr(record, "status", "") or "")
    if status == "ready":
        return _validate_ready_attempt(request, plan, record)
    record_id = str(getattr(record, "id", "") or "")
    if status != "pending":
        code = (
            ConversationContextErrorCode.CHECKPOINT_REQUIRED
            if status == "compressing"
            else ConversationContextErrorCode.CHECKPOINT_FAILED
        )
        raise ConversationContextError(
            code,
            "同一来源范围已有 checkpoint attempt，未重复调用整理模型。",
            details={"checkpoint_id": record_id, "status": status},
        )
    sources = checkpoint_sources_payload(
        new_source_messages=plan.source_messages,
        prior_checkpoint_id=active.checkpoint_id if active else None,
        prior_checkpoint_hash=active.checkpoint.fingerprint if active else None,
        conversation_kind=request.conversation.kind,
        execution_ledger=plan.folded_ledger,
        execution_source_hashes=request.execution_source_hashes,
    )
    added = request.store.add_context_checkpoint_sources(
        request.conversation.kind.value,
        request.conversation.id,
        record_id,
        sources,
        owner_id=request.owner_id,
    )
    if added is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源归属校验失败。",
        )
    record = request.store.update_context_checkpoint_status(
        request.conversation.kind.value,
        request.conversation.id,
        record_id,
        "compressing",
        owner_id=request.owner_id,
        expected_statuses=["pending"],
    )
    if record is None:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
            "checkpoint attempt 在启动前已被替换。",
        )
    commit_context_phase(request.store)
    return _Attempt(record_id, plan.expected_revision)


async def _emit_attempt_started(
    request: _GenerationRequest,
    attempt: _Attempt,
) -> None:
    context_payload = context_state_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        trigger="request_over_capacity",
    )
    checkpoint_payload = checkpoint_record_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        checkpoint_id=attempt.record_id,
    )
    # The projections above perform owner-scoped reads and therefore open an
    # implicit SQLAlchemy transaction.  Release it before awaiting an event
    # observer and, most importantly, before the subsequent model request.
    commit_context_phase(request.store)
    await emit_best_effort(
        request.event_sink,
        "conversation_context",
        {"context_state": context_payload},
    )
    await emit_best_effort(
        request.event_sink,
        "conversation_checkpoint",
        {"checkpoint": checkpoint_payload},
    )


async def _reload_and_validate_source(
    request: _GenerationRequest,
    plan: _GenerationPlan,
) -> None:
    result = request.reload_turns()
    reloaded = tuple(await result) if inspect.isawaitable(result) else tuple(result)
    by_id = {turn.turn_id: turn for turn in reloaded}
    segment = tuple(by_id.get(turn.turn_id) for turn in plan.segment_turns)
    if any(turn is None for turn in segment):
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源回合在生成期间发生变化。",
        )
    complete = tuple(turn for turn in segment if turn is not None)
    ineligible = next((turn for turn in complete if not turn.checkpoint_eligible), None)
    if ineligible is not None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源回合在生成期间不再是可整理的 completed 可见回合。",
            details={
                "turn_id": ineligible.turn_id,
                "status": ineligible.status.value,
            },
        )
    # The source range is immutable, but the transcript is append-only.  A
    # newer user may be persisted while the model is compacting this older
    # range.  Validate the complete reloaded projection against a synthetic
    # boundary after its newest closed turn instead of treating an append
    # outside ``plan.segment_range`` as a source mutation.
    newest_sequence = max(
        (
            message.sequence_no
            for turn in reloaded
            for message in turn.messages
        ),
        default=0,
    )
    validate_transcript_snapshot(
        reloaded,
        current_user_message=ConversationMessage(
            message_id=(
                f"checkpoint-current-boundary:{request.conversation.id}:"
                f"{newest_sequence + 1}"
            ),
            sequence_no=newest_sequence + 1,
            role="user",
            content="checkpoint boundary",
        ),
    )
    if source_range_for_turns(complete) != plan.segment_range:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源 hash 在生成期间发生变化。",
        )


def _materialize_checkpoint(
    request: _GenerationRequest,
    plan: _GenerationPlan,
    proposal: Any,
) -> ConversationCheckpoint:
    active = request.active
    new_quotes = materialize_author_quotes(proposal, source_messages=plan.source_messages)
    merged_quotes = rollup_author_quotes(
        proposal,
        previous_author_quotes=active.checkpoint.author_quotes if active else (),
        new_author_quotes=new_quotes,
    )
    checkpoint = ConversationCheckpoint(
        scope=request.conversation.kind,
        conversation_id=request.conversation.id,
        source_range=plan.segment_range,
        semantic_navigation=proposal.semantic_navigation,
        author_quotes=merged_quotes,
        execution_ledger=plan.folded_ledger,
        project_refs=project_references_from_execution_ledger(plan.folded_ledger),
        warnings=tuple(active.checkpoint.warnings if active else ()),
        segment_ids=_merge_segment_ids(active),
        policy_version=CONVERSATION_CONTEXT_POLICY_VERSION,
    )
    validate_checkpoint(
        checkpoint,
        source_messages=plan.source_messages,
        expected_scope=request.conversation.kind,
        expected_conversation_id=request.conversation.id,
        trusted_execution_ledger={
            entry.step_id: entry for entry in request.trusted_execution_ledger
        },
        trusted_author_quotes={
            (quote.message_id, quote.start_char, quote.end_char): quote
            for quote in (active.checkpoint.author_quotes if active else ())
        },
    )
    return checkpoint


def _persist_ready_checkpoint(
    request: _GenerationRequest,
    plan: _GenerationPlan,
    attempt: _Attempt,
    checkpoint: ConversationCheckpoint,
) -> ActiveCheckpoint:
    refresh_context_phase(request.store)
    current = request.store.context_checkpoint(
        request.conversation.kind.value,
        request.conversation.id,
        attempt.record_id,
        owner_id=request.owner_id,
    )
    if current is None:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
            "checkpoint attempt 已不存在。",
        )
    status = str(getattr(current, "status", "") or "")
    if status == "cancelled" or getattr(current, "cancel_requested_at", None):
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_CANCELLED,
            "checkpoint 整理已取消。",
        )
    if status != "compressing":
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
            "checkpoint attempt 已被其他请求替换。",
        )
    validation = {
        "schema": "conversation_checkpoint_validation.v1",
        "scope": request.conversation.kind.value,
        "conversation_id": request.conversation.id,
        "segment_ids": list(checkpoint.segment_ids),
        "warnings": list(checkpoint.warnings),
        "checkpoint_fingerprint": checkpoint.fingerprint,
    }
    ready = request.store.update_context_checkpoint_status(
        request.conversation.kind.value,
        request.conversation.id,
        attempt.record_id,
        "ready",
        owner_id=request.owner_id,
        expected_statuses=["compressing"],
        model_binding_json=request.binding.to_dict(),
        model_binding_fingerprint=request.binding.fingerprint,
        semantic_navigation_json=canonical_value(checkpoint.semantic_navigation),
        author_quotes_json=[canonical_value(item) for item in checkpoint.author_quotes],
        execution_ledger_json=[canonical_value(item) for item in checkpoint.execution_ledger],
        project_refs_json=[canonical_value(item) for item in checkpoint.project_refs],
        validation_json=validation,
        original_tokens=sum(_turn_cost(request.counter, turn) for turn in request.all_turns),
        checkpoint_tokens=request.counter.count_text(render_checkpoint_reference(checkpoint)),
        error_code=None,
        error_detail=None,
    )
    if ready is None:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
            "checkpoint ready 状态已被并发替换，当前任务未执行。",
        )
    publish_or_resolve_checkpoint_race(
        store=request.store,
        conversation=request.conversation,
        owner_id=request.owner_id,
        checkpoint_id=attempt.record_id,
        expected_revision=attempt.expected_revision,
    )
    commit_context_phase(request.store)
    return _active_result(request, plan, record=ready, checkpoint=checkpoint)


async def _emit_checkpoint_ready(
    request: _GenerationRequest,
    active: ActiveCheckpoint,
) -> None:
    checkpoint_payload = checkpoint_record_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        checkpoint_id=active.checkpoint_id,
    )
    context_payload = context_state_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        trigger="request_over_capacity",
    )
    # Do not hold the projection's read transaction while an SSE/event sink
    # is awaited.  This also leaves the session clean if another checkpoint
    # segment is planned immediately afterwards.
    commit_context_phase(request.store)
    await emit_best_effort(
        request.event_sink,
        "conversation_checkpoint",
        {
            "checkpoint": checkpoint_payload,
            "context_state": context_payload,
        },
    )


def _safe_checkpoint_error(exc: Exception) -> ConversationContextError:
    if isinstance(exc, ConversationContextError):
        return exc
    return ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_FAILED,
        "checkpoint 生成失败；本次任务未执行，请重试。",
    )


async def _fail_attempt(
    request: _GenerationRequest,
    attempt: _Attempt,
    exc: Exception,
) -> ConversationContextError:
    refresh_context_phase(request.store)
    current = request.store.context_checkpoint(
        request.conversation.kind.value,
        request.conversation.id,
        attempt.record_id,
        owner_id=request.owner_id,
    )
    status = str(getattr(current, "status", "") or "") if current else ""
    error = _safe_checkpoint_error(exc)
    if current is not None and status in {"pending", "compressing"}:
        target = (
            "cancelled"
            if error.code is ConversationContextErrorCode.CHECKPOINT_CANCELLED
            else "failed"
        )
        request.store.update_context_checkpoint_status(
            request.conversation.kind.value,
            request.conversation.id,
            attempt.record_id,
            target,
            owner_id=request.owner_id,
            expected_statuses=[status],
            error_code=error.code.value,
            error_detail=safe_public_error_detail(error.code),
        )
        commit_context_phase(request.store)
    checkpoint_payload = checkpoint_record_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        checkpoint_id=attempt.record_id,
    )
    context_payload = context_state_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        error=error,
    )
    commit_context_phase(request.store)
    await emit_best_effort(
        request.event_sink,
        "conversation_checkpoint",
        {
            "checkpoint": checkpoint_payload,
            "context_state": context_payload,
        },
    )
    return error


async def generate_checkpoint_segment(request: _GenerationRequest) -> ActiveCheckpoint:
    """Generate one sealed segment across explicit DB/network/DB phases."""

    plan = _plan_generation(request)
    created = _create_attempt(request, plan)
    if isinstance(created, ActiveCheckpoint):
        return created
    attempt = created
    try:
        await _emit_attempt_started(request, attempt)
        proposal = await _call_checkpoint_model(
            completion=request.completion,
            messages=plan.checkpoint_messages,
            binding=request.binding,
            counter=request.counter,
            safety_margin_tokens=request.safety_margin_tokens,
        )
        await _reload_and_validate_source(request, plan)
        checkpoint = _materialize_checkpoint(request, plan, proposal)
        result = _persist_ready_checkpoint(request, plan, attempt, checkpoint)
        await _emit_checkpoint_ready(request, result)
        return result
    except asyncio.CancelledError:
        mark_task_cancelled_checkpoint(
            store=request.store,
            conversation=request.conversation,
            owner_id=request.owner_id,
            checkpoint_id=attempt.record_id,
        )
        raise
    except Exception as exc:
        error = await _fail_attempt(request, attempt, exc)
        raise error from exc


__all__ = [
    "_GenerationRequest",
    "_call_checkpoint_model",
    "_default_checkpoint_completion",
    "_require_prior_quote_rollup_capacity",
    "generate_checkpoint_segment",
]
