"""Stable public facade for the staged conversation-context runtime.

Implementation lives in focused modules so assembly, provenance loading,
checkpoint generation, lifecycle projection, and orchestration can be audited
independently without creating parallel business paths.
"""

from .assembly import assemble_context_step, resolve_generation_model_binding
from .checkpoint_generation import (
    _call_checkpoint_model,
    _require_prior_quote_rollup_capacity,
)
from .checkpoint_loading import checkpoint_from_record, load_active_checkpoint
from .checkpoint_state import (
    cancel_checkpoint_attempt,
    checkpoint_record_payload,
    context_state_payload,
)
from .checkpoint_state import (
    publish_or_resolve_checkpoint_race as _publish_or_resolve_checkpoint_race,
)
from .preparation import prepare_conversation_context
from .runtime_types import (
    CONVERSATION_CONTEXT_POLICY_VERSION,
    ActiveCheckpoint,
    AssembledContextStep,
    ConversationContextStore,
    PreparedConversationContext,
)

__all__ = [
    "_call_checkpoint_model",
    "_publish_or_resolve_checkpoint_race",
    "_require_prior_quote_rollup_capacity",
    "ActiveCheckpoint",
    "AssembledContextStep",
    "CONVERSATION_CONTEXT_POLICY_VERSION",
    "ConversationContextStore",
    "PreparedConversationContext",
    "assemble_context_step",
    "cancel_checkpoint_attempt",
    "checkpoint_from_record",
    "checkpoint_record_payload",
    "context_state_payload",
    "load_active_checkpoint",
    "prepare_conversation_context",
    "resolve_generation_model_binding",
]
