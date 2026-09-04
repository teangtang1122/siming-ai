"""Pure model binding, budget preflight, and final context-frame assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.services.context_orchestrator import ContextOrchestrator

from .budget import (
    FallbackUtf8ByteTokenCounter,
    RequestBudgetEnvelope,
    RequestTokenComponents,
    TokenCounter,
    Utf8ByteTokenCounter,
    build_request_budget,
)
from .canonical import canonical_sha256, text_sha256
from .context_frame import ContextFrame, ContextFrameIntegrity
from .contracts import (
    CapacityAssurance,
    ConversationCheckpoint,
    ConversationIdentity,
    ConversationMessage,
    ConversationTurn,
    GenerationModelBinding,
    SystemContract,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .protocol_validator import ModelToolCapability, ToolProtocolValidator
from .provider_renderer import (
    ContextLayer,
    RenderedContextRequest,
    render_context_frame,
    render_historical_turn_status,
)
from .recent_turns import MandatoryExactTurnsOverCapacity, select_recent_turns
from .runtime_types import AssembledContextStep
from .tool_transactions import ToolExecutionReceipt, ToolTransaction, ToolTransactionState
from .transcript import source_range_for_turns, validate_transcript_snapshot


def effective_system_prompt(system_prompt: str, extra_runtime_instruction: str) -> str:
    instruction = str(extra_runtime_instruction or "").strip()
    if not instruction:
        return system_prompt
    return "\n\n".join(
        (
            system_prompt,
            "[SERVER_RUNTIME_INSTRUCTION]",
            "authority: server_current_turn",
            instruction,
            "[/SERVER_RUNTIME_INSTRUCTION]",
        )
    )


def resolve_generation_model_binding(
    *,
    orchestrator: ContextOrchestrator,
    model: str | None,
    task_type: str,
    protocol: str,
    system_prompt: str,
    current_tools: Sequence[Mapping[str, Any]],
) -> tuple[GenerationModelBinding, TokenCounter, int]:
    """Adapt the model profile, using the bounded 256K fallback when it is unknown."""

    profile = orchestrator.resolve_model_profile(model, task_type)
    if profile.known:
        counter: TokenCounter = Utf8ByteTokenCounter()
        assurance = CapacityAssurance.CONSERVATIVE
    else:
        counter = FallbackUtf8ByteTokenCounter()
        assurance = CapacityAssurance.UNVERIFIED
    binding = GenerationModelBinding.from_resolved_profile(
        profile,
        task_type=task_type,
        protocol=protocol,
        token_counter_id=counter.counter_id,
        capacity_assurance=assurance,
        prompt_contract_hash=text_sha256(system_prompt),
        tool_schema_hash=canonical_sha256(list(current_tools)),
        config_fingerprint=canonical_sha256(
            {
                "provider": profile.provider,
                "model_name": profile.model_name,
                "context_window_tokens": profile.context_window_tokens,
                "max_output_tokens": profile.max_output_tokens,
                "safety_margin_tokens": profile.safety_margin_tokens,
                "known": profile.known,
                "task_type": task_type,
                "protocol": protocol,
            }
        ),
    )
    return binding, counter, max(0, int(profile.safety_margin_tokens))


@dataclass(frozen=True)
class _AssemblyOptions:
    conversation: ConversationIdentity
    turns: tuple[ConversationTurn, ...]
    current_user_message: ConversationMessage
    model_binding: GenerationModelBinding
    token_counter: TokenCounter
    system_prompt: str
    current_tools: Sequence[Mapping[str, Any]]
    safety_margin_tokens: int
    active_checkpoint: ConversationCheckpoint | None
    checkpoint_segments: Sequence[ConversationCheckpoint]
    covered_sequence_ranges: Sequence[tuple[int, int]]
    active_tool_category_hash: str | None
    current_ledger: Sequence[ToolExecutionReceipt]
    delivered_transactions: Sequence[ToolTransaction]
    generator_template: str
    provider_wrapper: Any
    provider_protocol_state: Any
    provider_state: Any
    output_reserve_tokens: int | None
    max_model_visible_result_tokens_for_open_tools: int
    next_step_wrapper: Any
    model_capability: ModelToolCapability | None


def _empty_budget(options: _AssemblyOptions) -> RequestBudgetEnvelope:
    return build_request_budget(
        binding=options.model_binding,
        counter=options.token_counter,
        components=RequestTokenComponents(),
        output_reserve_tokens=options.output_reserve_tokens,
        safety_margin_tokens=options.safety_margin_tokens,
    )


def _covered_ranges(options: _AssemblyOptions) -> tuple[tuple[int, int], ...]:
    ranges = tuple(options.covered_sequence_ranges)
    if options.active_checkpoint is not None and not ranges:
        source = options.active_checkpoint.source_range
        return ((source.first_sequence, source.last_sequence),)
    return ranges


def _validate_options(options: _AssemblyOptions) -> None:
    snapshot = validate_transcript_snapshot(
        options.turns,
        current_user_message=options.current_user_message,
    )
    if options.conversation.revision != snapshot.revision:
        raise ValueError("conversation revision must equal current user message sequence")
    if options.model_binding.prompt_contract_hash != text_sha256(options.system_prompt):
        raise ValueError("model binding prompt hash does not match current system prompt")
    if options.model_binding.tool_schema_hash != canonical_sha256(list(options.current_tools)):
        raise ValueError("model binding tool schema hash does not match current tools")
    for transaction in options.delivered_transactions:
        if transaction.state is not ToolTransactionState.DELIVERED:
            raise ConversationContextError(
                ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
                "只有完整 delivered 工具事务可以进入下一模型步骤。",
                details={"transaction_id": transaction.transaction_id},
            )


def _provider_wrapper(options: _AssemblyOptions) -> Any:
    if options.provider_wrapper is not None:
        return options.provider_wrapper
    request_fields = ["messages", "temperature", "max_tokens"]
    if options.current_tools:
        request_fields.extend(("tools", "tool_choice"))
    return {
        "provider": options.model_binding.provider,
        "protocol": options.model_binding.protocol,
        "model": options.model_binding.model_name,
        "request_fields": request_fields,
    }


def _provisional_frame(
    options: _AssemblyOptions,
    *,
    recent_turns: Sequence[ConversationTurn] = (),
) -> ContextFrame:
    checkpoint = options.active_checkpoint
    category_hash = options.active_tool_category_hash or canonical_sha256(
        list(options.current_tools)
    )
    return ContextFrame(
        conversation=options.conversation,
        model_binding=options.model_binding,
        system_contract=SystemContract(
            prompt_hash=text_sha256(options.system_prompt),
            active_tool_category_hash=category_hash,
        ),
        checkpoint=checkpoint,
        recent_turns=tuple(recent_turns),
        current_user_message=options.current_user_message,
        current_turn_ledger=tuple(options.current_ledger),
        pending_tool_transactions=tuple(options.delivered_transactions),
        budget=_empty_budget(options),
        integrity=ContextFrameIntegrity(
            transcript_revision=options.conversation.revision,
            checkpoint_hash=checkpoint.fingerprint if checkpoint is not None else None,
        ),
        checkpoint_segments=tuple(options.checkpoint_segments),
    )


def _count_rendered_components(
    options: _AssemblyOptions,
    rendered: RenderedContextRequest,
) -> RequestTokenComponents:
    counter = options.token_counter
    layer_tokens = {layer: 0 for layer in ContextLayer}
    atomic = 0
    for message in rendered.messages:
        cost = counter.count_text(message.content)
        if message.tool_calls:
            cost += counter.count_value(list(message.tool_calls))
        if message.tool_call_id is not None:
            cost += counter.count_text(message.tool_call_id)
        layer_tokens[message.layer] += cost
        atomic += cost
    structural = max(0, counter.count_value(rendered.provider_messages()) - atomic)
    wrapper = _provider_wrapper(options)
    explicit_wrapper = counter.count_value(wrapper) if wrapper is not None else 0
    next_wrapper = options.next_step_wrapper
    next_wrapper_tokens = (
        max(0, next_wrapper)
        if isinstance(next_wrapper, int) and not isinstance(next_wrapper, bool)
        else counter.count_value(next_wrapper)
        if next_wrapper is not None
        else 0
    )
    return RequestTokenComponents(
        system_prompt_tokens=layer_tokens[ContextLayer.SYSTEM_CONTRACT],
        generator_template_tokens=counter.count_text(options.generator_template),
        tool_schema_tokens=counter.count_value(list(options.current_tools)),
        message_wrapper_tokens=structural + explicit_wrapper,
        provider_protocol_tokens=(
            counter.count_value(options.provider_protocol_state)
            if options.provider_protocol_state is not None
            else 0
        ),
        checkpoint_tokens=layer_tokens[ContextLayer.HISTORICAL_REFERENCE],
        recent_exact_turn_tokens=layer_tokens[ContextLayer.RECENT_EXACT_TURN],
        current_user_tokens=layer_tokens[ContextLayer.CURRENT_USER],
        current_turn_ledger_tokens=layer_tokens[ContextLayer.CURRENT_TURN_LEDGER],
        pending_tool_transaction_tokens=layer_tokens[ContextLayer.PENDING_TOOL_TRANSACTION],
        provider_state_tokens=(
            counter.count_value(options.provider_state) if options.provider_state is not None else 0
        ),
        extra_runtime_instruction_tokens=0,
        max_model_visible_result_tokens_for_open_tools=max(
            0, int(options.max_model_visible_result_tokens_for_open_tools)
        ),
        next_step_wrapper_tokens=next_wrapper_tokens,
    )


def _build_budget(
    options: _AssemblyOptions,
    frame: ContextFrame,
) -> tuple[RenderedContextRequest, RequestTokenComponents, RequestBudgetEnvelope]:
    rendered = render_context_frame(
        frame,
        system_prompt=options.system_prompt,
        require_sendable=False,
    )
    components = _count_rendered_components(options, rendered)
    budget = build_request_budget(
        binding=options.model_binding,
        counter=options.token_counter,
        components=components,
        output_reserve_tokens=options.output_reserve_tokens,
        safety_margin_tokens=options.safety_margin_tokens,
    )
    return rendered, components, budget


def _require_base_sendable(
    options: _AssemblyOptions,
    components: RequestTokenComponents,
    budget: RequestBudgetEnvelope,
) -> None:
    try:
        budget.require_sendable()
    except ConversationContextError as exc:
        if exc.code is ConversationContextErrorCode.CAPACITY_UNKNOWN:
            raise ConversationContextError(
                ConversationContextErrorCode.CAPACITY_UNKNOWN,
                "当前模型缺少可验证的容量档案；请在“设置 → 上下文治理”中确认并保存。",
                details={
                    **exc.details,
                    "provider": options.model_binding.provider,
                    "model": options.model_binding.model_name,
                    "remediation": "configure_model_context_profile",
                },
            ) from exc
        required = (
            components.checkpoint_tokens
            + components.current_turn_ledger_tokens
            + components.pending_tool_transaction_tokens
            + components.provider_state_tokens
        )
        non_required = budget.current_input_tokens - required
        if (
            exc.code is ConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY
            and required > 0
            and non_required <= budget.request_input_limit
        ):
            raise ConversationContextError(
                ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
                "活动 checkpoint 或未完成协议状态超过模型容量，不能静默删除。",
                details={
                    "required_state_tokens": required,
                    "current_input_tokens": budget.current_input_tokens,
                    "request_input_limit": budget.request_input_limit,
                },
            ) from exc
        raise


def _turn_cost(counter: TokenCounter, turn: ConversationTurn) -> int:
    messages = [
        {"role": message.role.value, "content": message.content} for message in turn.messages
    ]
    status_receipt = render_historical_turn_status(turn)
    if status_receipt is not None:
        messages.append({"role": "assistant", "content": status_receipt})
    return counter.count_value(messages)


def _select_exact_turns(
    options: _AssemblyOptions,
    budget: RequestBudgetEnvelope,
    components: RequestTokenComponents,
) -> tuple[ConversationTurn, ...]:
    growth = (
        components.max_model_visible_result_tokens_for_open_tools
        + components.next_step_wrapper_tokens
    )
    available = budget.request_input_limit - budget.current_input_tokens - growth
    if available < 0:
        raise ConversationContextError(
            ConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY,
            "当前工具可能返回的结果无法在下一步安全送入模型。",
            details={
                "projected_next_step_tokens": budget.projected_next_step_tokens,
                "request_input_limit": budget.request_input_limit,
            },
        )
    try:
        selection = select_recent_turns(
            options.turns,
            available_tokens=available,
            count_turn_tokens=lambda turn: _turn_cost(options.token_counter, turn),
            covered_sequence_ranges=_covered_ranges(options),
        )
    except MandatoryExactTurnsOverCapacity as exc:
        raise ConversationContextError(
            ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
            "异常、取消或不可整理的历史回合必须保留原文，但已超过模型容量。",
            details={"available_tokens": available},
        ) from exc
    if selection.checkpoint_turns:
        segment = [selection.checkpoint_turns[0]]
        for turn in selection.checkpoint_turns[1:]:
            if turn.messages[0].sequence_no != segment[-1].messages[-1].sequence_no + 1:
                break
            segment.append(turn)
        source = source_range_for_turns(segment)
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_REQUIRED,
            "较早完整回合需要先整理为 checkpoint。",
            details={
                "turn_ids": [turn.turn_id for turn in segment],
                "first_sequence": source.first_sequence,
                "last_sequence": source.last_sequence,
                "message_count": source.message_count,
                "source_hash": source.source_hash,
                "trigger": (
                    "projected_next_step_over_capacity" if growth else "request_over_capacity"
                ),
            },
        )
    return tuple(selection.exact_turns)


def _seal_final_step(
    options: _AssemblyOptions,
    exact_turns: tuple[ConversationTurn, ...],
) -> AssembledContextStep:
    provisional = _provisional_frame(options, recent_turns=exact_turns)
    _, _, budget = _build_budget(options, provisional)
    budget.require_sendable()
    if not budget.fits_projected:
        raise ConversationContextError(
            ConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY,
            "下一步工具结果预算超过模型容量。",
            details=budget.to_dict(),
        )
    frame = replace(provisional, budget=budget).sealed()
    rendered = render_context_frame(frame, system_prompt=options.system_prompt)
    capability = options.model_capability or ModelToolCapability(
        supports_native_tool_calling=bool(options.current_tools)
    )
    ToolProtocolValidator.validate(
        rendered.validation_messages(),
        capability=capability,
        tools_enabled=bool(options.current_tools or options.delivered_transactions),
        current_user_message_id=rendered.current_user_message_id,
        checkpoint_message_id=rendered.checkpoint_message_id,
    )
    return AssembledContextStep(frame, rendered, budget, ())


def assemble_context_step(
    *,
    conversation: ConversationIdentity,
    turns: tuple[ConversationTurn, ...],
    current_user_message: ConversationMessage,
    model_binding: GenerationModelBinding,
    token_counter: TokenCounter,
    system_prompt: str,
    current_tools: Sequence[Mapping[str, Any]],
    safety_margin_tokens: int,
    active_checkpoint: ConversationCheckpoint | None = None,
    checkpoint_segments: Sequence[ConversationCheckpoint] = (),
    covered_sequence_ranges: Sequence[tuple[int, int]] = (),
    active_tool_category_hash: str | None = None,
    current_ledger: Sequence[ToolExecutionReceipt] = (),
    delivered_transactions: Sequence[ToolTransaction] = (),
    generator_template: str = "",
    provider_wrapper: Any = None,
    provider_protocol_state: Any = None,
    provider_state: Any = None,
    extra_runtime_instruction: str = "",
    output_reserve_tokens: int | None = None,
    max_model_visible_result_tokens_for_open_tools: int = 0,
    next_step_wrapper: Any = None,
    model_capability: ModelToolCapability | None = None,
) -> AssembledContextStep:
    """Assemble and seal the only provider request for one model step."""

    options = _AssemblyOptions(
        conversation=conversation,
        turns=turns,
        current_user_message=current_user_message,
        model_binding=model_binding,
        token_counter=token_counter,
        system_prompt=effective_system_prompt(system_prompt, extra_runtime_instruction),
        current_tools=current_tools,
        safety_margin_tokens=safety_margin_tokens,
        active_checkpoint=active_checkpoint,
        checkpoint_segments=checkpoint_segments,
        covered_sequence_ranges=covered_sequence_ranges,
        active_tool_category_hash=active_tool_category_hash,
        current_ledger=current_ledger,
        delivered_transactions=delivered_transactions,
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
    )
    _validate_options(options)
    base = _provisional_frame(options)
    _, components, budget = _build_budget(options, base)
    _require_base_sendable(options, components, budget)
    exact_turns = _select_exact_turns(options, budget, components)
    return _seal_final_step(options, exact_turns)


__all__ = [
    "_turn_cost",
    "assemble_context_step",
    "effective_system_prompt",
    "resolve_generation_model_binding",
]
