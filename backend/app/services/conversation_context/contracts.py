"""Immutable contracts for durable and active conversation context."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .canonical import canonical_sha256, canonical_value


class CapacityAssurance(StrEnum):
    EXACT = "exact"
    CONSERVATIVE = "conservative"
    UNVERIFIED = "unverified"


class ConversationKind(StrEnum):
    WORKSPACE = "workspace"
    CREATION = "creation"


class ConversationRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    RUNNING = "running"
    ERROR = "error"
    ABORTED = "aborted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GenerationModelBinding:
    """Immutable capacity identity resolved by the existing model runtime.

    This object intentionally does not resolve a provider or guess a model
    window.  Callers adapt the authoritative runtime/profile result into this
    contract and explicitly state how capacity was assured.
    """

    task_type: str
    provider: str
    model_name: str
    normalized_model: str
    protocol: str
    context_window_tokens: int
    max_output_tokens: int
    token_counter_id: str
    capacity_assurance: CapacityAssurance
    prompt_contract_hash: str
    tool_schema_hash: str
    config_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capacity_assurance",
            CapacityAssurance(self.capacity_assurance),
        )
        for name in (
            "task_type",
            "provider",
            "model_name",
            "normalized_model",
            "protocol",
            "prompt_contract_hash",
            "tool_schema_hash",
            "config_fingerprint",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} must not be empty")
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if self.max_output_tokens < 0:
            raise ValueError("max_output_tokens must not be negative")
        if (
            self.capacity_assurance is not CapacityAssurance.UNVERIFIED
            and not self.token_counter_id
        ):
            raise ValueError("verified capacity requires token_counter_id")

    @classmethod
    def from_resolved_profile(
        cls,
        profile: Any,
        *,
        task_type: str,
        protocol: str,
        token_counter_id: str,
        capacity_assurance: CapacityAssurance,
        prompt_contract_hash: str,
        tool_schema_hash: str,
        config_fingerprint: str,
    ) -> GenerationModelBinding:
        """Adapt an existing ``ResolvedModelContextProfile``-like object.

        Structural access keeps this package independent of SQLAlchemy while
        ensuring integrations reuse the current runtime's provider, model and
        capacity resolution instead of reimplementing it here.
        """

        provider = str(profile.provider)
        model_name = str(profile.model_name)
        assurance = capacity_assurance
        if not bool(getattr(profile, "known", False)):
            assurance = CapacityAssurance.UNVERIFIED
        return cls(
            task_type=task_type,
            provider=provider,
            model_name=model_name,
            normalized_model=f"{provider}:{model_name}",
            protocol=protocol,
            context_window_tokens=int(profile.context_window_tokens),
            max_output_tokens=max(0, int(profile.max_output_tokens or 0)),
            token_counter_id=token_counter_id,
            capacity_assurance=assurance,
            prompt_contract_hash=prompt_contract_hash,
            tool_schema_hash=tool_schema_hash,
            config_fingerprint=config_fingerprint,
        )

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class ConversationIdentity:
    kind: ConversationKind
    id: str
    revision: int
    project_id: str | None = None
    creation_session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ConversationKind(self.kind))
        if not self.id:
            raise ValueError("conversation id must not be empty")
        if self.revision < 0:
            raise ValueError("conversation revision must not be negative")
        if self.kind is ConversationKind.WORKSPACE and not self.project_id:
            raise ValueError("workspace conversation requires project_id")
        if self.kind is ConversationKind.CREATION and not self.creation_session_id:
            raise ValueError("creation conversation requires creation_session_id")


@dataclass(frozen=True)
class SystemContract:
    prompt_hash: str
    active_tool_category_hash: str

    def __post_init__(self) -> None:
        if not self.prompt_hash or not self.active_tool_category_hash:
            raise ValueError("system contract hashes must not be empty")


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    sequence_no: int
    role: ConversationRole
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", ConversationRole(self.role))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if not self.message_id:
            raise ValueError("message_id must not be empty")
        if self.sequence_no <= 0:
            raise ValueError("sequence_no must be positive")
        if self.role is ConversationRole.TOOL and not self.tool_call_id:
            raise ValueError("tool message requires tool_call_id")
        if self.role is not ConversationRole.TOOL and self.tool_call_id is not None:
            raise ValueError("only tool messages may set tool_call_id")
        if self.tool_calls and self.role is not ConversationRole.ASSISTANT:
            raise ValueError("only assistant messages may contain tool_calls")

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    status: TurnStatus
    messages: tuple[ConversationMessage, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", TurnStatus(self.status))
        object.__setattr__(self, "messages", tuple(self.messages))
        if not self.turn_id:
            raise ValueError("turn_id must not be empty")
        if not self.messages:
            raise ValueError("turn must contain at least one message")
        sequences = [message.sequence_no for message in self.messages]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("turn messages must have unique ascending sequence_no")

    @property
    def closed(self) -> bool:
        return self.status in {
            TurnStatus.COMPLETED,
            TurnStatus.ERROR,
            TurnStatus.ABORTED,
            TurnStatus.CANCELLED,
        }

    @property
    def safe_visible_projection(self) -> bool:
        """Whether this is one exact, provider-safe cross-turn projection.

        Native tool protocol is deliberately not reconstructed across turns.
        A workspace or Creation adapter may project only the author's exact
        user message and the final exact assistant message into historical
        context; raw calls and results remain in the durable transcript and
        RunStep audit records.
        """

        if len(self.messages) != 2:
            return False
        user, assistant = self.messages
        return (
            user.role is ConversationRole.USER
            and assistant.role is ConversationRole.ASSISTANT
            and bool(user.content.strip())
            and bool(assistant.content.strip())
            and assistant.sequence_no == user.sequence_no + 1
            and not user.tool_calls
            and not assistant.tool_calls
            and user.tool_call_id is None
            and assistant.tool_call_id is None
        )

    @property
    def checkpoint_eligible(self) -> bool:
        """Only semantically complete visible turns may be summarized."""

        return self.status is TurnStatus.COMPLETED and self.safe_visible_projection


@dataclass(frozen=True)
class SourceRange:
    first_sequence: int
    last_sequence: int
    message_count: int
    source_hash: str

    def __post_init__(self) -> None:
        if self.first_sequence <= 0 or self.last_sequence < self.first_sequence:
            raise ValueError("invalid checkpoint source sequence range")
        if self.message_count <= 0:
            raise ValueError("checkpoint source range must contain messages")
        if not self.source_hash:
            raise ValueError("checkpoint source_hash must not be empty")


@dataclass(frozen=True)
class SemanticNavigation:
    authority: str = "non_authoritative_navigation"
    current_objectives: tuple[str, ...] = ()
    resolved_decisions: tuple[str, ...] = ()
    superseded_directions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    next_context_needed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "current_objectives",
            "resolved_decisions",
            "superseded_directions",
            "unresolved_questions",
            "next_context_needed",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.authority != "non_authoritative_navigation":
            raise ValueError("semantic navigation must be non-authoritative")


@dataclass(frozen=True)
class AuthorQuote:
    message_id: str
    start_char: int
    end_char: int
    exact_quote: str
    quote_sha256: str
    purpose: str
    superseded: bool = False

    def __post_init__(self) -> None:
        if not self.message_id or not self.purpose:
            raise ValueError("author quote message_id and purpose are required")
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError("invalid author quote range")
        if not self.exact_quote or not self.quote_sha256:
            raise ValueError("author quote text and hash are required")
        if not isinstance(self.superseded, bool):
            raise ValueError("author quote superseded must be boolean")


@dataclass(frozen=True)
class ResourceReference:
    type: str
    id: str
    revision: int | str | None = None

    def __post_init__(self) -> None:
        if not self.type or not self.id:
            raise ValueError("resource reference type and id are required")


@dataclass(frozen=True)
class ExecutionLedgerEntry:
    run_id: str
    step_id: str
    tool: str
    status: str
    resource_refs: tuple[ResourceReference, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_refs", tuple(self.resource_refs))
        if not self.run_id or not self.step_id or not self.tool or not self.status:
            raise ValueError("execution ledger identity fields are required")


@dataclass(frozen=True)
class ProjectReference:
    type: str
    id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.type or not self.id or not self.reason:
            raise ValueError("project reference fields are required")


@dataclass(frozen=True)
class ConversationCheckpoint:
    scope: ConversationKind
    conversation_id: str
    source_range: SourceRange
    semantic_navigation: SemanticNavigation = field(default_factory=SemanticNavigation)
    author_quotes: tuple[AuthorQuote, ...] = ()
    execution_ledger: tuple[ExecutionLedgerEntry, ...] = ()
    project_refs: tuple[ProjectReference, ...] = ()
    warnings: tuple[str, ...] = ()
    segment_ids: tuple[str, ...] = ()
    policy_version: int = 1
    schema: str = "conversation_checkpoint.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", ConversationKind(self.scope))
        object.__setattr__(self, "author_quotes", tuple(self.author_quotes))
        object.__setattr__(self, "execution_ledger", tuple(self.execution_ledger))
        object.__setattr__(self, "project_refs", tuple(self.project_refs))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "segment_ids", tuple(self.segment_ids))
        if self.schema != "conversation_checkpoint.v1":
            raise ValueError("unsupported conversation checkpoint schema")
        if not self.conversation_id:
            raise ValueError("checkpoint conversation_id must not be empty")
        if self.policy_version <= 0:
            raise ValueError("checkpoint policy_version must be positive")
        if len(self.segment_ids) != len(set(self.segment_ids)):
            raise ValueError("checkpoint segment_ids must be unique")

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


__all__ = [
    "AuthorQuote",
    "CapacityAssurance",
    "ConversationCheckpoint",
    "ConversationIdentity",
    "ConversationKind",
    "ConversationMessage",
    "ConversationRole",
    "ConversationTurn",
    "ExecutionLedgerEntry",
    "GenerationModelBinding",
    "ProjectReference",
    "ResourceReference",
    "SemanticNavigation",
    "SourceRange",
    "SystemContract",
    "TurnStatus",
]
