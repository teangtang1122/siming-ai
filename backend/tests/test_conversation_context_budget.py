"""Unit coverage for model binding and complete request budget arithmetic."""

from types import SimpleNamespace

import pytest

from app.services.conversation_context import (
    CallableTokenCounter,
    CapacityAssurance,
    ConversationContextError,
    ConversationContextErrorCode,
    FallbackUtf8ByteTokenCounter,
    GenerationModelBinding,
    RequestTokenComponents,
    UnverifiedEstimateTokenCounter,
    Utf8ByteTokenCounter,
    build_request_budget,
)


def _binding(
    *,
    assurance: CapacityAssurance = CapacityAssurance.CONSERVATIVE,
    counter_id: str = "conservative.utf8_bytes.v1",
    context_window: int = 1_000,
    max_output: int = 100,
) -> GenerationModelBinding:
    return GenerationModelBinding(
        task_type="assistant",
        provider="openai",
        model_name="test-model",
        normalized_model="openai:test-model",
        protocol="chat_completions",
        context_window_tokens=context_window,
        max_output_tokens=max_output,
        token_counter_id=counter_id,
        capacity_assurance=assurance,
        prompt_contract_hash="prompt-hash",
        tool_schema_hash="tool-hash",
        config_fingerprint="config-hash",
    )


def test_existing_resolved_profile_is_adapted_without_re_resolving_model() -> None:
    profile = SimpleNamespace(
        provider="anthropic",
        model_name="claude-test",
        context_window_tokens=200_000,
        max_output_tokens=8_192,
        known=True,
    )
    binding = GenerationModelBinding.from_resolved_profile(
        profile,
        task_type="assistant",
        protocol="messages",
        token_counter_id="anthropic:test-counter",
        capacity_assurance=CapacityAssurance.EXACT,
        prompt_contract_hash="prompt",
        tool_schema_hash="tools",
        config_fingerprint="config",
    )

    assert binding.normalized_model == "anthropic:claude-test"
    assert binding.context_window_tokens == 200_000
    assert binding.capacity_assurance is CapacityAssurance.EXACT


def test_unknown_existing_profile_forces_unverified_capacity() -> None:
    profile = SimpleNamespace(
        provider="unknown",
        model_name="custom",
        context_window_tokens=1_000_000,
        max_output_tokens=16_000,
        known=False,
    )
    binding = GenerationModelBinding.from_resolved_profile(
        profile,
        task_type="assistant",
        protocol="chat_completions",
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash="prompt",
        tool_schema_hash="tools",
        config_fingerprint="config",
    )

    assert binding.capacity_assurance is CapacityAssurance.UNVERIFIED


def test_utf8_counter_is_conservative_and_legacy_estimator_is_unverified() -> None:
    conservative = Utf8ByteTokenCounter()
    legacy = UnverifiedEstimateTokenCounter()

    assert conservative.count_text("司命 A") == len("司命 A".encode())
    assert conservative.assurance is CapacityAssurance.CONSERVATIVE
    assert legacy.count_text("司命 A") > 0
    assert legacy.assurance is CapacityAssurance.UNVERIFIED


def test_budget_counts_every_component_and_projected_growth() -> None:
    binding = _binding()
    counter = Utf8ByteTokenCounter()
    components = RequestTokenComponents(
        system_prompt_tokens=10,
        generator_template_tokens=11,
        tool_schema_tokens=12,
        message_wrapper_tokens=13,
        provider_protocol_tokens=14,
        checkpoint_tokens=15,
        recent_exact_turn_tokens=16,
        current_user_tokens=17,
        current_turn_ledger_tokens=18,
        pending_tool_transaction_tokens=19,
        provider_state_tokens=20,
        extra_runtime_instruction_tokens=21,
        max_model_visible_result_tokens_for_open_tools=300,
        next_step_wrapper_tokens=22,
    )

    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=components,
        safety_margin_tokens=50,
    )

    assert budget.request_input_limit == 850
    assert budget.current_input_tokens == sum(range(10, 22))
    assert budget.projected_next_step_tokens == budget.current_input_tokens + 322
    assert budget.fits_current is True
    assert budget.fits_projected is True
    budget.require_sendable()


def test_unverified_budget_cannot_be_used_as_hard_capacity() -> None:
    counter = UnverifiedEstimateTokenCounter()
    binding = _binding(
        assurance=CapacityAssurance.UNVERIFIED,
        counter_id=counter.counter_id,
    )
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=10),
        safety_margin_tokens=50,
    )

    with pytest.raises(ConversationContextError) as caught:
        budget.require_sendable()
    assert caught.value.code is ConversationContextErrorCode.CAPACITY_UNKNOWN


def test_bounded_256k_fallback_is_sendable_without_becoming_verified() -> None:
    counter = FallbackUtf8ByteTokenCounter()
    binding = _binding(
        assurance=CapacityAssurance.UNVERIFIED,
        counter_id=counter.counter_id,
        context_window=256_000,
        max_output=16_000,
    )
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=10),
        safety_margin_tokens=512,
    )

    assert budget.verified is False
    assert budget.bounded_fallback is True
    budget.require_sendable()


def test_budget_rejects_counter_that_does_not_match_binding() -> None:
    binding = _binding(counter_id="runtime:exact")
    counter = CallableTokenCounter(
        counter_id="another:exact",
        assurance=CapacityAssurance.CONSERVATIVE,
        text_counter=len,
    )
    with pytest.raises(ValueError, match="does not match"):
        build_request_budget(
            binding=binding,
            counter=counter,
            components=RequestTokenComponents(),
            safety_margin_tokens=0,
        )


def test_current_user_message_is_never_truncated_to_fit() -> None:
    binding = _binding(context_window=100, max_output=20)
    counter = Utf8ByteTokenCounter()
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=90),
        safety_margin_tokens=10,
    )

    with pytest.raises(ConversationContextError) as caught:
        budget.require_sendable()
    assert caught.value.code is ConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY
