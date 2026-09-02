"""Token counter boundary and auditable full-request budget envelope."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ...core.model_limits import DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS
from ..rag.context_packer import estimate_tokens
from .canonical import canonical_json, canonical_value
from .contracts import CapacityAssurance, GenerationModelBinding
from .errors import ConversationContextError, ConversationContextErrorCode


@runtime_checkable
class TokenCounter(Protocol):
    """Provider adapter contract for counting the actual rendered request.

    Exact and conservative implementations belong next to the provider/model
    runtime that owns the tokenizer.  Conversation context consumes this
    interface and never guesses a model from its name.
    """

    @property
    def counter_id(self) -> str: ...

    @property
    def assurance(self) -> CapacityAssurance: ...

    def count_text(self, text: str) -> int: ...

    def count_value(self, value: Any) -> int: ...


@dataclass(frozen=True)
class CallableTokenCounter:
    """Adapter for a tokenizer/message counter supplied by model_runtime."""

    counter_id: str
    assurance: CapacityAssurance
    text_counter: Callable[[str], int]
    value_counter: Callable[[Any], int] | None = None

    def __post_init__(self) -> None:
        if not self.counter_id:
            raise ValueError("counter_id must not be empty")
        if self.assurance is CapacityAssurance.UNVERIFIED:
            raise ValueError("use UnverifiedEstimateTokenCounter for estimates")

    def count_text(self, text: str) -> int:
        return max(0, int(self.text_counter(str(text or ""))))

    def count_value(self, value: Any) -> int:
        if self.value_counter is not None:
            return max(0, int(self.value_counter(value)))
        return self.count_text(canonical_json(value))


@dataclass(frozen=True)
class UnverifiedEstimateTokenCounter:
    """Existing RAG estimator exposed honestly for preview/telemetry only."""

    counter_id: str = "legacy.rag_estimate.v1"
    assurance: CapacityAssurance = CapacityAssurance.UNVERIFIED

    def count_text(self, text: str) -> int:
        return estimate_tokens(str(text or ""))

    def count_value(self, value: Any) -> int:
        return self.count_text(canonical_json(value))


FALLBACK_UTF8_BYTE_TOKEN_COUNTER_ID = "fallback.utf8_bytes.v1"


@dataclass(frozen=True)
class FallbackUtf8ByteTokenCounter:
    """Conservative byte count paired with the bounded unverified fallback window."""

    counter_id: str = FALLBACK_UTF8_BYTE_TOKEN_COUNTER_ID
    assurance: CapacityAssurance = CapacityAssurance.UNVERIFIED

    def count_text(self, text: str) -> int:
        return len(str(text or "").encode("utf-8"))

    def count_value(self, value: Any) -> int:
        return self.count_text(canonical_json(value))


@dataclass(frozen=True)
class Utf8ByteTokenCounter:
    """Provider-neutral conservative upper bound for rendered UTF-8 data.

    The integration must count the *actual rendered* prompt, messages and tool
    schema and must add provider-only wrapper/protocol tokens separately.  The
    byte length is intentionally not advertised as exact.  An unknown model
    window remains unverified even when this counter is available.
    """

    counter_id: str = "conservative.utf8_bytes.v1"
    assurance: CapacityAssurance = CapacityAssurance.CONSERVATIVE

    def count_text(self, text: str) -> int:
        return len(str(text or "").encode("utf-8"))

    def count_value(self, value: Any) -> int:
        return self.count_text(canonical_json(value))


@dataclass(frozen=True)
class RequestTokenComponents:
    """Every token-bearing logical section in one rendered Agent request."""

    system_prompt_tokens: int = 0
    generator_template_tokens: int = 0
    tool_schema_tokens: int = 0
    message_wrapper_tokens: int = 0
    provider_protocol_tokens: int = 0
    checkpoint_tokens: int = 0
    recent_exact_turn_tokens: int = 0
    current_user_tokens: int = 0
    current_turn_ledger_tokens: int = 0
    pending_tool_transaction_tokens: int = 0
    provider_state_tokens: int = 0
    extra_runtime_instruction_tokens: int = 0
    max_model_visible_result_tokens_for_open_tools: int = 0
    next_step_wrapper_tokens: int = 0

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if value < 0:
                raise ValueError(f"{name} must not be negative")

    @property
    def current_input_tokens(self) -> int:
        return sum(
            (
                self.system_prompt_tokens,
                self.generator_template_tokens,
                self.tool_schema_tokens,
                self.message_wrapper_tokens,
                self.provider_protocol_tokens,
                self.checkpoint_tokens,
                self.recent_exact_turn_tokens,
                self.current_user_tokens,
                self.current_turn_ledger_tokens,
                self.pending_tool_transaction_tokens,
                self.provider_state_tokens,
                self.extra_runtime_instruction_tokens,
            )
        )


@dataclass(frozen=True)
class RequestBudgetEnvelope:
    schema: str
    model_binding_fingerprint: str
    token_counter_id: str
    capacity_assurance: CapacityAssurance
    context_window_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    system_prompt_tokens: int
    generator_template_tokens: int
    tool_schema_tokens: int
    message_wrapper_tokens: int
    provider_protocol_tokens: int
    checkpoint_tokens: int
    recent_exact_turn_tokens: int
    current_user_tokens: int
    current_turn_ledger_tokens: int
    pending_tool_transaction_tokens: int
    provider_state_tokens: int
    extra_runtime_instruction_tokens: int
    max_model_visible_result_tokens_for_open_tools: int
    next_step_wrapper_tokens: int
    current_input_tokens: int
    request_input_limit: int
    projected_next_step_tokens: int
    fits_current: bool
    fits_projected: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capacity_assurance",
            CapacityAssurance(self.capacity_assurance),
        )
        if self.schema != "request_budget_envelope.v1":
            raise ValueError("unsupported request budget schema")
        if not self.model_binding_fingerprint or not self.token_counter_id:
            raise ValueError("budget binding and token counter are required")

    @property
    def verified(self) -> bool:
        return self.capacity_assurance in {
            CapacityAssurance.EXACT,
            CapacityAssurance.CONSERVATIVE,
        }

    @property
    def bounded_fallback(self) -> bool:
        return (
            self.capacity_assurance is CapacityAssurance.UNVERIFIED
            and self.token_counter_id == FALLBACK_UTF8_BYTE_TOKEN_COUNTER_ID
            and 0 < self.context_window_tokens <= DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS
        )

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)

    def require_sendable(self) -> None:
        if not self.verified and not self.bounded_fallback:
            raise ConversationContextError(
                ConversationContextErrorCode.CAPACITY_UNKNOWN,
                "当前模型缺少可验证的 Token 计数与容量档案。",
                details={
                    "capacity_assurance": self.capacity_assurance.value,
                    "token_counter_id": self.token_counter_id,
                },
            )
        if self.current_user_tokens > self.request_input_limit:
            raise ConversationContextError(
                ConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY,
                "当前用户消息自身超过模型可用输入容量，不能截断后执行。",
                details={
                    "current_user_tokens": self.current_user_tokens,
                    "request_input_limit": self.request_input_limit,
                },
            )
        if not self.fits_current:
            raise ConversationContextError(
                ConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY,
                "最终 Agent 请求超过绑定模型的可验证容量。",
                details={
                    "current_input_tokens": self.current_input_tokens,
                    "request_input_limit": self.request_input_limit,
                },
            )


def build_request_budget(
    *,
    binding: GenerationModelBinding,
    counter: TokenCounter,
    components: RequestTokenComponents,
    output_reserve_tokens: int | None = None,
    safety_margin_tokens: int,
) -> RequestBudgetEnvelope:
    """Build a budget from already-counted rendered sections.

    The provider adapter is responsible for using ``counter`` on the actual
    prompt/messages/tools/protocol representation.  This function verifies
    that the counter matches the immutable model binding and performs the only
    capacity arithmetic used by conversation context.
    """

    if binding.token_counter_id != counter.counter_id:
        raise ValueError("token counter does not match generation model binding")
    if binding.capacity_assurance is not counter.assurance:
        raise ValueError("token counter assurance does not match model binding")
    output = binding.max_output_tokens if output_reserve_tokens is None else output_reserve_tokens
    output = max(0, int(output))
    margin = max(0, int(safety_margin_tokens))
    input_limit = max(0, binding.context_window_tokens - output - margin)
    current = components.current_input_tokens
    projected = (
        current
        + components.max_model_visible_result_tokens_for_open_tools
        + components.next_step_wrapper_tokens
    )
    return RequestBudgetEnvelope(
        schema="request_budget_envelope.v1",
        model_binding_fingerprint=binding.fingerprint,
        token_counter_id=counter.counter_id,
        capacity_assurance=counter.assurance,
        context_window_tokens=binding.context_window_tokens,
        output_reserve_tokens=output,
        safety_margin_tokens=margin,
        system_prompt_tokens=components.system_prompt_tokens,
        generator_template_tokens=components.generator_template_tokens,
        tool_schema_tokens=components.tool_schema_tokens,
        message_wrapper_tokens=components.message_wrapper_tokens,
        provider_protocol_tokens=components.provider_protocol_tokens,
        checkpoint_tokens=components.checkpoint_tokens,
        recent_exact_turn_tokens=components.recent_exact_turn_tokens,
        current_user_tokens=components.current_user_tokens,
        current_turn_ledger_tokens=components.current_turn_ledger_tokens,
        pending_tool_transaction_tokens=components.pending_tool_transaction_tokens,
        provider_state_tokens=components.provider_state_tokens,
        extra_runtime_instruction_tokens=components.extra_runtime_instruction_tokens,
        max_model_visible_result_tokens_for_open_tools=(
            components.max_model_visible_result_tokens_for_open_tools
        ),
        next_step_wrapper_tokens=components.next_step_wrapper_tokens,
        current_input_tokens=current,
        request_input_limit=input_limit,
        projected_next_step_tokens=projected,
        fits_current=current <= input_limit,
        fits_projected=projected <= input_limit,
    )


__all__ = [
    "CallableTokenCounter",
    "FallbackUtf8ByteTokenCounter",
    "RequestBudgetEnvelope",
    "RequestTokenComponents",
    "TokenCounter",
    "UnverifiedEstimateTokenCounter",
    "Utf8ByteTokenCounter",
    "build_request_budget",
]
