"""Provider-neutral conversation context contracts and validators.

The package deliberately contains no database or model-provider access.  It is
shared by the workspace and creation agents; adapters are responsible for
loading durable records, resolving the generation model, and rendering a
provider request.
"""

from .budget import (
    CallableTokenCounter,
    FallbackUtf8ByteTokenCounter,
    RequestBudgetEnvelope,
    RequestTokenComponents,
    TokenCounter,
    UnverifiedEstimateTokenCounter,
    Utf8ByteTokenCounter,
    build_request_budget,
)
from .checkpoint_prompt import (
    CHECKPOINT_NAVIGATION_SCHEMA,
    AuthorQuotePosition,
    CheckpointNavigationProposal,
    PriorAuthorQuoteDecision,
    build_checkpoint_messages,
    build_checkpoint_repair_messages,
    checkpoint_navigation_json_schema,
    materialize_author_quotes,
    parse_checkpoint_navigation,
    rollup_author_quotes,
)
from .checkpoint_renderer import render_checkpoint_reference
from .checkpoint_validator import (
    CheckpointSourceMessage,
    checkpoint_source_hash,
    validate_checkpoint,
)
from .codec import checkpoint_from_dict, context_frame_from_dict
from .context_frame import ContextFrame, ContextFrameIntegrity
from .contracts import (
    AuthorQuote,
    CapacityAssurance,
    ConversationCheckpoint,
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationTurn,
    ExecutionLedgerEntry,
    GenerationModelBinding,
    ProjectReference,
    ResourceReference,
    SemanticNavigation,
    SourceRange,
    SystemContract,
)
from .errors import ConversationContextError, ConversationContextErrorCode
from .execution_ledger import (
    execution_ledger_from_run_steps,
    execution_source_hashes_from_run_steps,
    fold_execution_ledger,
    project_references_from_execution_ledger,
    resource_references_from_run_step,
    tool_receipts_from_run_steps,
)
from .protocol_validator import ModelToolCapability, ToolProtocolValidator
from .provider_renderer import (
    ContextLayer,
    RenderedContextMessage,
    RenderedContextRequest,
    render_context_frame,
)
from .recent_turns import (
    MandatoryExactTurnsOverCapacity,
    RecentTurnSelection,
    select_recent_turns,
)
from .reference_context import (
    ReferenceContext,
    render_reference_context_system_segment,
)
from .runtime import (
    CONVERSATION_CONTEXT_POLICY_VERSION,
    ActiveCheckpoint,
    AssembledContextStep,
    PreparedConversationContext,
    assemble_context_step,
    cancel_checkpoint_attempt,
    checkpoint_from_record,
    checkpoint_record_payload,
    context_state_payload,
    load_active_checkpoint,
    prepare_conversation_context,
    resolve_generation_model_binding,
)
from .tool_transactions import (
    NativeToolCall,
    NativeToolResult,
    ToolExecutionReceipt,
    ToolTransaction,
    ToolTransactionState,
)
from .transcript import (
    TranscriptSnapshot,
    checkpoint_source_messages,
    source_range_for_turns,
    turns_after_checkpoint,
    validate_transcript_snapshot,
)

__all__ = [
    "AuthorQuote",
    "AuthorQuotePosition",
    "ActiveCheckpoint",
    "AssembledContextStep",
    "CHECKPOINT_NAVIGATION_SCHEMA",
    "CONVERSATION_CONTEXT_POLICY_VERSION",
    "CallableTokenCounter",
    "CapacityAssurance",
    "CheckpointSourceMessage",
    "CheckpointNavigationProposal",
    "ContextFrame",
    "ContextFrameIntegrity",
    "ContextLayer",
    "ConversationCheckpoint",
    "ConversationContextError",
    "ConversationContextErrorCode",
    "ConversationIdentity",
    "ConversationKind",
    "ConversationMessage",
    "ConversationTurn",
    "ExecutionLedgerEntry",
    "FallbackUtf8ByteTokenCounter",
    "GenerationModelBinding",
    "MandatoryExactTurnsOverCapacity",
    "ModelToolCapability",
    "NativeToolCall",
    "NativeToolResult",
    "ProjectReference",
    "PreparedConversationContext",
    "PriorAuthorQuoteDecision",
    "RecentTurnSelection",
    "RenderedContextMessage",
    "RenderedContextRequest",
    "RequestBudgetEnvelope",
    "ReferenceContext",
    "RequestTokenComponents",
    "ResourceReference",
    "SemanticNavigation",
    "SourceRange",
    "SystemContract",
    "TokenCounter",
    "ToolExecutionReceipt",
    "ToolProtocolValidator",
    "ToolTransaction",
    "ToolTransactionState",
    "TranscriptSnapshot",
    "UnverifiedEstimateTokenCounter",
    "Utf8ByteTokenCounter",
    "assemble_context_step",
    "build_request_budget",
    "build_checkpoint_messages",
    "build_checkpoint_repair_messages",
    "cancel_checkpoint_attempt",
    "checkpoint_from_record",
    "checkpoint_navigation_json_schema",
    "checkpoint_record_payload",
    "checkpoint_source_messages",
    "checkpoint_source_hash",
    "checkpoint_from_dict",
    "context_frame_from_dict",
    "context_state_payload",
    "execution_ledger_from_run_steps",
    "execution_source_hashes_from_run_steps",
    "fold_execution_ledger",
    "load_active_checkpoint",
    "materialize_author_quotes",
    "parse_checkpoint_navigation",
    "prepare_conversation_context",
    "project_references_from_execution_ledger",
    "resolve_generation_model_binding",
    "resource_references_from_run_step",
    "render_checkpoint_reference",
    "render_context_frame",
    "render_reference_context_system_segment",
    "rollup_author_quotes",
    "select_recent_turns",
    "source_range_for_turns",
    "tool_receipts_from_run_steps",
    "turns_after_checkpoint",
    "validate_checkpoint",
    "validate_transcript_snapshot",
]
