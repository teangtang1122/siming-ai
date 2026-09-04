"""Top-level orchestration for deterministic context preparation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.context_orchestrator import ContextOrchestrator

from .assembly import (
    _turn_cost,
    assemble_context_step,
    effective_system_prompt,
    resolve_generation_model_binding,
)
from .checkpoint_generation import (
    _default_checkpoint_completion,
    _GenerationRequest,
    generate_checkpoint_segment,
)
from .checkpoint_loading import load_active_checkpoint_with_stale_recovery
from .checkpoint_state import checkpoint_record_payload, context_state_payload
from .contracts import (
    ConversationIdentity,
    ConversationMessage,
    ConversationTurn,
    ExecutionLedgerEntry,
    TurnStatus,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .protocol_validator import ModelToolCapability
from .runtime_types import (
    CheckpointCompletion,
    ContextEventSink,
    ConversationContextStore,
    PreparedConversationContext,
    TurnReloader,
)
from .store_phases import emit_best_effort, refresh_context_phase
from .tool_transactions import ToolExecutionReceipt, ToolTransaction
from .transcript import TranscriptSnapshot, validate_transcript_snapshot


@dataclass(frozen=True)
class _PreparationRequest:
    store: ConversationContextStore
    orchestrator: ContextOrchestrator
    conversation: ConversationIdentity
    owner_id: str
    turns: tuple[ConversationTurn, ...]
    current_user_message: ConversationMessage
    model: str | None
    task_type: str
    protocol: str
    system_prompt: str
    current_tools: Sequence[Mapping[str, Any]]
    reload_turns: TurnReloader
    active_tool_category_hash: str | None
    current_ledger: Sequence[ToolExecutionReceipt]
    delivered_transactions: Sequence[ToolTransaction]
    trusted_execution_ledger: Sequence[ExecutionLedgerEntry]
    execution_source_hashes: Mapping[str, str]
    generator_template: str
    provider_wrapper: Any
    provider_protocol_state: Any
    provider_state: Any
    output_reserve_tokens: int | None
    max_model_visible_result_tokens_for_open_tools: int
    next_step_wrapper: Any
    model_capability: ModelToolCapability | None
    checkpoint_completion: CheckpointCompletion | None
    event_sink: ContextEventSink | None


def _desired_checkpoint_turns(
    snapshot: TranscriptSnapshot,
    error: ConversationContextError,
) -> tuple[ConversationTurn, ...]:
    wanted = {str(item) for item in error.details.get("turn_ids") or ()}
    desired = tuple(turn for turn in snapshot.turns if turn.turn_id in wanted)
    if desired:
        return desired
    raise ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_FAILED,
        "checkpoint 规划没有返回可验证的完整回合。",
    ) from error


async def _emit_prepare_error(
    request: _PreparationRequest,
    error: ConversationContextError,
) -> None:
    try:
        payload = context_state_payload(
            store=request.store,
            conversation_kind=request.conversation.kind,
            conversation_id=request.conversation.id,
            owner_id=request.owner_id,
            error=error,
        )
        await emit_best_effort(
            request.event_sink,
            "conversation_context",
            {"context_state": payload},
        )
    except Exception:
        return


def _assemble(
    request: _PreparationRequest,
    *,
    snapshot: TranscriptSnapshot,
    active,
    binding,
    counter,
    safety_margin_tokens: int,
    capability: ModelToolCapability,
):
    return assemble_context_step(
        conversation=request.conversation,
        turns=snapshot.turns,
        current_user_message=request.current_user_message,
        model_binding=binding,
        token_counter=counter,
        system_prompt=request.system_prompt,
        current_tools=request.current_tools,
        safety_margin_tokens=safety_margin_tokens,
        active_checkpoint=active.checkpoint if active is not None else None,
        checkpoint_segments=active.checkpoint_chain if active is not None else (),
        covered_sequence_ranges=(active.covered_sequence_ranges if active is not None else ()),
        active_tool_category_hash=request.active_tool_category_hash,
        current_ledger=request.current_ledger,
        delivered_transactions=request.delivered_transactions,
        generator_template=request.generator_template,
        provider_wrapper=request.provider_wrapper,
        provider_protocol_state=request.provider_protocol_state,
        provider_state=request.provider_state,
        output_reserve_tokens=request.output_reserve_tokens,
        max_model_visible_result_tokens_for_open_tools=(
            request.max_model_visible_result_tokens_for_open_tools
        ),
        next_step_wrapper=request.next_step_wrapper,
        model_capability=capability,
    )


async def _generate_required_checkpoint(
    request: _PreparationRequest,
    *,
    snapshot: TranscriptSnapshot,
    desired: tuple[ConversationTurn, ...],
    active,
    state,
    binding,
    counter,
    safety_margin_tokens: int,
    completion: CheckpointCompletion,
):
    return await generate_checkpoint_segment(
        _GenerationRequest(
            store=request.store,
            conversation=request.conversation,
            owner_id=request.owner_id,
            all_turns=snapshot.turns,
            desired_turns=desired,
            active=active,
            state=state,
            binding=binding,
            counter=counter,
            safety_margin_tokens=safety_margin_tokens,
            trusted_execution_ledger=tuple(request.trusted_execution_ledger),
            execution_source_hashes=request.execution_source_hashes,
            reload_turns=request.reload_turns,
            completion=completion,
            event_sink=request.event_sink,
        )
    )


async def _finalize_prepared(
    request: _PreparationRequest,
    *,
    snapshot: TranscriptSnapshot,
    state,
    active,
    step,
    counter,
    trigger: str,
) -> PreparedConversationContext:
    original_tokens = sum(_turn_cost(counter, turn) for turn in snapshot.turns)
    active_tokens = step.budget.checkpoint_tokens + step.budget.recent_exact_turn_tokens
    metrics = {
        **step.budget.to_dict(),
        "trigger": trigger,
        "recent_exact_turn_count": len(step.frame.recent_turns),
        "original_history_tokens": original_tokens,
        "active_history_tokens": active_tokens,
        "warnings": [],
    }
    if state is not None and hasattr(state, "last_budget_json"):
        state.last_budget_json = metrics
        if hasattr(state, "updated_at"):
            state.updated_at = datetime.utcnow()
    state_payload = context_state_payload(
        store=request.store,
        conversation_kind=request.conversation.kind,
        conversation_id=request.conversation.id,
        owner_id=request.owner_id,
        trigger=trigger,
        recent_exact_turn_count=len(step.frame.recent_turns),
        original_history_tokens=original_tokens,
        active_history_tokens=active_tokens,
    )
    detail = (
        checkpoint_record_payload(
            store=request.store,
            conversation_kind=request.conversation.kind,
            conversation_id=request.conversation.id,
            owner_id=request.owner_id,
            checkpoint_id=active.checkpoint_id,
        )
        if active is not None
        else None
    )
    await emit_best_effort(
        request.event_sink,
        "conversation_context",
        {"context_state": state_payload},
    )
    return PreparedConversationContext(step, state_payload, detail, trigger)


async def _prepare(request: _PreparationRequest) -> PreparedConversationContext:
    snapshot = validate_transcript_snapshot(
        request.turns,
        current_user_message=request.current_user_message,
    )
    if request.conversation.revision != snapshot.revision:
        raise ValueError("conversation revision must equal current user message sequence")
    binding, counter, margin = resolve_generation_model_binding(
        orchestrator=request.orchestrator,
        model=request.model,
        task_type=request.task_type,
        protocol=request.protocol,
        system_prompt=request.system_prompt,
        current_tools=request.current_tools,
    )
    state, active = load_active_checkpoint_with_stale_recovery(
        store=request.store,
        conversation=request.conversation,
        owner_id=request.owner_id,
        turns=snapshot.turns,
        trusted_execution_ledger=request.trusted_execution_ledger,
        execution_source_hashes=request.execution_source_hashes,
    )
    capability = request.model_capability or ModelToolCapability(
        supports_native_tool_calling=bool(request.current_tools),
        direct_mcp_validated=request.protocol == "direct_mcp",
    )
    completion = request.checkpoint_completion or _default_checkpoint_completion()
    trigger = "within_capacity"
    limit = sum(1 for turn in snapshot.turns if turn.status is TurnStatus.COMPLETED) + 1
    for _ in range(limit):
        try:
            step = _assemble(
                request,
                snapshot=snapshot,
                active=active,
                binding=binding,
                counter=counter,
                safety_margin_tokens=margin,
                capability=capability,
            )
        except ConversationContextError as exc:
            if exc.code is not ConversationContextErrorCode.CHECKPOINT_REQUIRED:
                await _emit_prepare_error(request, exc)
                raise
            trigger = str(exc.details.get("trigger") or "request_over_capacity")
            active = await _generate_required_checkpoint(
                request,
                snapshot=snapshot,
                desired=_desired_checkpoint_turns(snapshot, exc),
                active=active,
                state=state,
                binding=binding,
                counter=counter,
                safety_margin_tokens=margin,
                completion=completion,
            )
            refresh_context_phase(request.store)
            state = request.store.context_state(
                request.conversation.kind.value,
                request.conversation.id,
                owner_id=request.owner_id,
            )
            if state is None:
                raise ConversationContextError(
                    ConversationContextErrorCode.SOURCE_CHANGED,
                    "checkpoint 发布后 context state 丢失。",
                ) from exc
            continue
        return await _finalize_prepared(
            request,
            snapshot=snapshot,
            state=state,
            active=active,
            step=step,
            counter=counter,
            trigger=trigger,
        )
    raise ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_FAILED,
        "checkpoint 分段次数超过完整历史回合数，当前任务未执行。",
    )


async def prepare_conversation_context(
    *,
    store: ConversationContextStore,
    orchestrator: ContextOrchestrator,
    conversation: ConversationIdentity,
    owner_id: str,
    turns: tuple[ConversationTurn, ...],
    current_user_message: ConversationMessage,
    model: str | None,
    task_type: str,
    protocol: str,
    system_prompt: str,
    current_tools: Sequence[Mapping[str, Any]],
    reload_turns: TurnReloader,
    active_tool_category_hash: str | None = None,
    current_ledger: Sequence[ToolExecutionReceipt] = (),
    delivered_transactions: Sequence[ToolTransaction] = (),
    trusted_execution_ledger: Sequence[ExecutionLedgerEntry] = (),
    execution_source_hashes: Mapping[str, str] | None = None,
    generator_template: str = "",
    provider_wrapper: Any = None,
    provider_protocol_state: Any = None,
    provider_state: Any = None,
    extra_runtime_instruction: str = "",
    output_reserve_tokens: int | None = None,
    max_model_visible_result_tokens_for_open_tools: int = 0,
    next_step_wrapper: Any = None,
    model_capability: ModelToolCapability | None = None,
    checkpoint_completion: CheckpointCompletion | None = None,
    event_sink: ContextEventSink | None = None,
) -> PreparedConversationContext:
    """Prepare the only active frame used by workspace and Creation agents."""

    effective_prompt = effective_system_prompt(system_prompt, extra_runtime_instruction)
    return await _prepare(
        _PreparationRequest(
            store=store,
            orchestrator=orchestrator,
            conversation=conversation,
            owner_id=owner_id,
            turns=turns,
            current_user_message=current_user_message,
            model=model,
            task_type=task_type,
            protocol=protocol,
            system_prompt=effective_prompt,
            current_tools=current_tools,
            reload_turns=reload_turns,
            active_tool_category_hash=active_tool_category_hash,
            current_ledger=current_ledger,
            delivered_transactions=delivered_transactions,
            trusted_execution_ledger=trusted_execution_ledger,
            execution_source_hashes=dict(execution_source_hashes or {}),
            generator_template=generator_template,
            provider_wrapper=provider_wrapper,
            provider_protocol_state=provider_protocol_state,
            provider_state=provider_state,
            output_reserve_tokens=output_reserve_tokens,
            max_model_visible_result_tokens_for_open_tools=(
                max_model_visible_result_tokens_for_open_tools
            ),
            next_step_wrapper=next_step_wrapper,
            model_capability=model_capability,
            checkpoint_completion=checkpoint_completion,
            event_sink=event_sink,
        )
    )


__all__ = ["prepare_conversation_context"]
