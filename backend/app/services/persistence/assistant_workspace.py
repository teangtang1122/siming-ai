"""SQLAlchemy adapter for workspace-assistant persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session
from app.database.models import (
    AssistantConversation,
    AssistantMemory,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    Chapter,
)
from app.database.models_support import generate_uuid
from app.modules.assistant.infrastructure.models import (
    ConversationContextCheckpoint,
    ConversationContextCheckpointSource,
    ConversationContextState,
    SystemAssistantConversation,
    reserve_message_sequence_range,
)
from app.services.persistence.conversation_context_workspace import (
    checkpoint_identity_filters,
    checkpoint_source_belongs,
    state_identity_filters,
)

_CONTEXT_KINDS = {"workspace", "creation"}
_CHECKPOINT_STATUSES = {
    "pending",
    "compressing",
    "ready",
    "failed",
    "cancelled",
    "superseded",
}
_CHECKPOINT_TRANSITIONS = {
    "pending": {"compressing", "failed", "cancelled", "superseded"},
    "compressing": {"ready", "failed", "cancelled", "superseded"},
    "ready": {"superseded"},
    "failed": set(),
    "cancelled": set(),
    "superseded": set(),
}
_CHECKPOINT_MUTABLE_FIELDS = {
    "model_binding_json",
    "model_binding_fingerprint",
    "semantic_navigation_json",
    "author_quotes_json",
    "execution_ledger_json",
    "project_refs_json",
    "validation_json",
    "original_tokens",
    "checkpoint_tokens",
    "error_code",
    "error_detail",
    "cancel_requested_at",
}


class SqlAlchemyAssistantWorkspace:
    def __init__(self, session: Session) -> None:
        self.db = session

    def commit_context_phase(self) -> None:
        """Commit one explicit conversation-context persistence phase."""

        commit_session(self.db)

    def refresh_context_phase(self) -> None:
        """Discard the identity-map snapshot before a CAS reload."""

        self.db.expire_all()

    def conversation(self, project_id: str, conversation_id: str):
        return (
            self.db.query(AssistantConversation)
            .filter(
                AssistantConversation.id == conversation_id,
                AssistantConversation.project_id == project_id,
            )
            .first()
        )

    def conversation_by_canonical(self, project_id: str, canonical_conversation_id: str):
        return (
            self.db.query(AssistantConversation)
            .filter(
                AssistantConversation.canonical_conversation_id == canonical_conversation_id,
                AssistantConversation.project_id == project_id,
            )
            .first()
        )

    def create_conversation(self, **values: Any):
        conversation = AssistantConversation(**values)
        self.db.add(conversation)
        return conversation

    def create_message(self, **values: Any):
        conversation_id = str(values.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        reserved_sequence = reserve_message_sequence_range(
            self.db,
            conversation_model=AssistantConversation,
            message_model=AssistantMessage,
            conversation_id=conversation_id,
            count=1,
        )[0]
        explicit_sequence = values.pop("sequence_no", None)
        if explicit_sequence is not None and int(explicit_sequence) != reserved_sequence:
            raise ValueError("sequence_no must be the next conversation sequence")
        values["sequence_no"] = reserved_sequence
        message = AssistantMessage(**values)
        self.db.add(message)
        # Persist the reservation before another message is allocated in the
        # same autoflush=False session. The transaction remains uncommitted.
        self.db.flush()
        return message

    def message(self, message_id: str):
        return self.db.query(AssistantMessage).filter(AssistantMessage.id == message_id).first()

    def conversation_messages(self, conversation_id: str):
        return (
            self.db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id == conversation_id)
            .order_by(
                AssistantMessage.sequence_no.asc(),
            )
            .all()
        )

    def previous_assistant_messages(self, conversation_id: str):
        return (
            self.db.query(AssistantMessage)
            .filter(
                AssistantMessage.conversation_id == conversation_id,
                AssistantMessage.role == "assistant",
                AssistantMessage.status.in_({"completed", "running"}),
            )
            .order_by(AssistantMessage.sequence_no.desc())
            .all()
        )

    @staticmethod
    def _checkpoint_identity_filters(
        conversation_kind: str,
        conversation_id: str,
    ) -> tuple[Any, ...]:
        return checkpoint_identity_filters(conversation_kind, conversation_id)

    @staticmethod
    def _state_identity_filters(
        conversation_kind: str,
        conversation_id: str,
    ) -> tuple[Any, ...]:
        return state_identity_filters(conversation_kind, conversation_id)

    def _owned_context_conversation(
        self,
        conversation_kind: str,
        conversation_id: str,
        owner_id: str,
    ) -> AssistantConversation | SystemAssistantConversation | None:
        if conversation_kind not in _CONTEXT_KINDS:
            return None
        if conversation_kind == "workspace":
            return (
                self.db.query(AssistantConversation)
                .filter(
                    AssistantConversation.id == conversation_id,
                    AssistantConversation.project_id == owner_id,
                )
                .first()
            )
        return (
            self.db.query(SystemAssistantConversation)
            .filter(
                SystemAssistantConversation.id == conversation_id,
                SystemAssistantConversation.scope_type == "creation",
                SystemAssistantConversation.scope_id == owner_id,
            )
            .first()
        )

    def context_state(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> ConversationContextState | None:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return None
        return (
            self.db.query(ConversationContextState)
            .filter(*self._state_identity_filters(conversation_kind, conversation_id))
            .first()
        )

    def ensure_context_state(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> ConversationContextState | None:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return None
        state = (
            self.db.query(ConversationContextState)
            .filter(*self._state_identity_filters(conversation_kind, conversation_id))
            .first()
        )
        if state is not None:
            return state

        now = datetime.utcnow()
        values: dict[str, Any] = {
            "id": generate_uuid(),
            "conversation_kind": conversation_kind,
            "assistant_conversation_id": (
                conversation_id if conversation_kind == "workspace" else None
            ),
            "system_conversation_id": (
                conversation_id if conversation_kind == "creation" else None
            ),
            "revision": 0,
            "active_checkpoint_id": None,
            "active_source_last_sequence": 0,
            "last_budget_json": {},
            "last_compacted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        dialect = self.db.get_bind().dialect.name
        if dialect == "sqlite":
            conflict_column = (
                "assistant_conversation_id"
                if conversation_kind == "workspace"
                else "system_conversation_id"
            )
            self.db.execute(
                sqlite_insert(ConversationContextState)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[conflict_column])
            )
        elif dialect == "postgresql":
            constraint = (
                "uq_context_state_assistant_conversation"
                if conversation_kind == "workspace"
                else "uq_context_state_system_conversation"
            )
            self.db.execute(
                postgresql_insert(ConversationContextState)
                .values(**values)
                .on_conflict_do_nothing(constraint=constraint)
            )
        else:
            state = ConversationContextState(**values)
            self.db.add(state)
            self.db.flush()
            return state
        self.db.flush()
        return (
            self.db.query(ConversationContextState)
            .filter(*self._state_identity_filters(conversation_kind, conversation_id))
            .one()
        )

    def context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> ConversationContextCheckpoint | None:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return None
        return (
            self.db.query(ConversationContextCheckpoint)
            .filter(
                ConversationContextCheckpoint.id == checkpoint_id,
                *self._checkpoint_identity_filters(conversation_kind, conversation_id),
            )
            .first()
        )

    def context_checkpoints(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> Sequence[ConversationContextCheckpoint]:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return []
        return (
            self.db.query(ConversationContextCheckpoint)
            .filter(*self._checkpoint_identity_filters(conversation_kind, conversation_id))
            .order_by(
                ConversationContextCheckpoint.source_first_sequence.asc(),
                ConversationContextCheckpoint.created_at.asc(),
                ConversationContextCheckpoint.id.asc(),
            )
            .all()
        )

    def create_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
        **values: Any,
    ) -> ConversationContextCheckpoint | None:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return None
        reserved_fields = {
            "conversation_kind",
            "assistant_conversation_id",
            "system_conversation_id",
        }
        if reserved_fields.intersection(values):
            raise ValueError("checkpoint conversation identity is assigned by the repository")
        idempotency_key = str(values.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        existing = (
            self.db.query(ConversationContextCheckpoint)
            .filter(ConversationContextCheckpoint.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            belongs = all(
                bool(expression)
                for expression in (
                    existing.conversation_kind == conversation_kind,
                    existing.assistant_conversation_id
                    == (conversation_id if conversation_kind == "workspace" else None),
                    existing.system_conversation_id
                    == (conversation_id if conversation_kind == "creation" else None),
                )
            )
            return existing if belongs else None

        status = str(values.get("status") or "pending")
        if status not in _CHECKPOINT_STATUSES:
            raise ValueError("invalid checkpoint status")
        first_sequence = int(values.get("source_first_sequence") or 0)
        last_sequence = int(values.get("source_last_sequence") or 0)
        message_count = int(values.get("source_message_count") or 0)
        if first_sequence <= 0 or last_sequence < first_sequence or message_count <= 0:
            raise ValueError("invalid checkpoint source range")
        source_hash = str(values.get("source_hash") or "").strip().lower()
        if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
            raise ValueError("source_hash must be a lowercase SHA-256 digest")
        values["source_hash"] = source_hash
        parent_checkpoint_id = values.get("parent_checkpoint_id")
        if parent_checkpoint_id and not self.context_checkpoint(
            conversation_kind,
            conversation_id,
            str(parent_checkpoint_id),
            owner_id=owner_id,
        ):
            return None
        if not self.ensure_context_state(conversation_kind, conversation_id, owner_id=owner_id):
            return None

        now = datetime.utcnow()
        self.db.query(ConversationContextCheckpoint).filter(
            *self._checkpoint_identity_filters(conversation_kind, conversation_id),
            ConversationContextCheckpoint.source_first_sequence == first_sequence,
            ConversationContextCheckpoint.source_last_sequence == last_sequence,
            # A concurrent request with the same idempotency key is the same
            # durable attempt, not an older attempt to supersede.  Excluding
            # it prevents the losing insert from changing the winner to
            # ``superseded`` immediately before the unique-key savepoint
            # resolves the race back to that same row.
            ConversationContextCheckpoint.idempotency_key != idempotency_key,
            ConversationContextCheckpoint.status.in_({"pending", "compressing"}),
        ).update(
            {
                ConversationContextCheckpoint.status: "superseded",
                ConversationContextCheckpoint.completed_at: now,
                ConversationContextCheckpoint.updated_at: now,
            },
            synchronize_session="fetch",
        )
        checkpoint = ConversationContextCheckpoint(
            conversation_kind=conversation_kind,
            assistant_conversation_id=(
                conversation_id if conversation_kind == "workspace" else None
            ),
            system_conversation_id=(conversation_id if conversation_kind == "creation" else None),
            **values,
        )
        try:
            # The global idempotency constraint is the cross-process arbiter.
            # A savepoint keeps the caller's transcript transaction usable
            # when two requests race after both observed no existing row.
            with self.db.begin_nested():
                self.db.add(checkpoint)
                self.db.flush()
        except IntegrityError:
            existing = (
                self.db.query(ConversationContextCheckpoint)
                .filter(ConversationContextCheckpoint.idempotency_key == idempotency_key)
                .first()
            )
            if existing is None:
                raise
            belongs = (
                existing.conversation_kind == conversation_kind
                and existing.assistant_conversation_id
                == (conversation_id if conversation_kind == "workspace" else None)
                and existing.system_conversation_id
                == (conversation_id if conversation_kind == "creation" else None)
            )
            return existing if belongs else None
        return checkpoint

    def _checkpoint_source_belongs(
        self,
        conversation_kind: str,
        conversation_id: str,
        source: dict[str, Any],
    ) -> bool:
        return checkpoint_source_belongs(
            self.db,
            conversation_kind,
            conversation_id,
            source,
        )

    def add_context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        sources: Sequence[dict[str, Any]],
        *,
        owner_id: str,
    ) -> Sequence[ConversationContextCheckpointSource] | None:
        checkpoint = self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )
        if checkpoint is None:
            return None
        pending: list[ConversationContextCheckpointSource] = []
        existing_by_identity = {
            (item.source_kind, item.source_id): item
            for item in self.db.query(ConversationContextCheckpointSource)
            .filter(ConversationContextCheckpointSource.checkpoint_id == checkpoint.id)
            .all()
        }
        for source in sources:
            source_kind = str(source.get("source_kind") or "")
            source_id = str(source.get("source_id") or "")
            source_hash = str(source.get("source_hash") or "").strip().lower()
            source_sequence = source.get("source_sequence")
            if (
                len(source_hash) != 64
                or any(char not in "0123456789abcdef" for char in source_hash)
                or not self._checkpoint_source_belongs(conversation_kind, conversation_id, source)
            ):
                return None
            identity = (source_kind, source_id)
            existing = existing_by_identity.get(identity)
            if existing is not None:
                if (
                    existing.source_hash != source_hash
                    or existing.source_sequence != source_sequence
                ):
                    return None
                continue
            item = ConversationContextCheckpointSource(
                checkpoint_id=checkpoint.id,
                source_kind=source_kind,
                source_id=source_id,
                source_sequence=(int(source_sequence) if source_sequence is not None else None),
                source_hash=source_hash,
            )
            pending.append(item)
            existing_by_identity[identity] = item
        if pending:
            self.db.add_all(pending)
            self.db.flush()
        return self.context_checkpoint_sources(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )

    def context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> Sequence[ConversationContextCheckpointSource]:
        checkpoint = self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )
        if checkpoint is None:
            return []
        return (
            self.db.query(ConversationContextCheckpointSource)
            .filter(ConversationContextCheckpointSource.checkpoint_id == checkpoint.id)
            .order_by(
                ConversationContextCheckpointSource.source_sequence.is_(None),
                ConversationContextCheckpointSource.source_sequence.asc(),
                ConversationContextCheckpointSource.created_at.asc(),
                ConversationContextCheckpointSource.id.asc(),
            )
            .all()
        )

    def update_context_checkpoint_status(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        new_status: str,
        *,
        owner_id: str,
        expected_statuses: Sequence[str] | None = None,
        **values: Any,
    ) -> ConversationContextCheckpoint | None:
        checkpoint = self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )
        if checkpoint is None:
            return None
        if new_status not in _CHECKPOINT_STATUSES:
            raise ValueError("invalid checkpoint status")
        expected = (
            set(expected_statuses) if expected_statuses is not None else {str(checkpoint.status)}
        )
        if checkpoint.status not in expected:
            return None
        if (
            new_status != checkpoint.status
            and new_status not in _CHECKPOINT_TRANSITIONS[checkpoint.status]
        ):
            return None
        unexpected_fields = set(values) - _CHECKPOINT_MUTABLE_FIELDS
        if unexpected_fields:
            raise ValueError(
                "unsupported checkpoint update fields: " + ", ".join(sorted(unexpected_fields))
            )
        now = datetime.utcnow()
        update_values: dict[Any, Any] = {
            ConversationContextCheckpoint.status: new_status,
            ConversationContextCheckpoint.updated_at: now,
        }
        for field, value in values.items():
            update_values[getattr(ConversationContextCheckpoint, field)] = value
        if new_status in {"ready", "failed", "cancelled", "superseded"}:
            update_values[ConversationContextCheckpoint.completed_at] = now

        filters = [
            ConversationContextCheckpoint.id == checkpoint_id,
            *self._checkpoint_identity_filters(conversation_kind, conversation_id),
        ]
        filters.append(ConversationContextCheckpoint.status.in_(expected))
        updated = (
            self.db.query(ConversationContextCheckpoint)
            .filter(*filters)
            .update(update_values, synchronize_session=False)
        )
        if updated != 1:
            self.db.expire(checkpoint)
            return None
        self.db.flush()
        self.db.expire(checkpoint)
        return self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )

    def invalidate_active_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        error_code: str,
        error_detail: str,
    ) -> bool:
        """CAS-clear one stale active pointer while retaining its audit record.

        A stale checkpoint is derived data.  The transcript stays untouched and
        the checkpoint transitions to ``superseded`` only when the caller still
        owns the exact active pointer/revision it validated.  A concurrent
        publisher therefore wins deterministically instead of being cleared by
        a late stale-reader.
        """

        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return False
        state = self.context_state(
            conversation_kind,
            conversation_id,
            owner_id=owner_id,
        )
        if (
            state is None
            or state.revision != expected_revision
            or str(state.active_checkpoint_id or "") != checkpoint_id
        ):
            return False
        now = datetime.utcnow()
        updated = (
            self.db.query(ConversationContextState)
            .filter(
                ConversationContextState.id == state.id,
                ConversationContextState.revision == expected_revision,
                ConversationContextState.active_checkpoint_id == checkpoint_id,
                *self._state_identity_filters(conversation_kind, conversation_id),
            )
            .update(
                {
                    ConversationContextState.active_checkpoint_id: None,
                    ConversationContextState.active_source_last_sequence: 0,
                    ConversationContextState.revision: expected_revision + 1,
                    ConversationContextState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            self.db.expire(state)
            return False
        superseded = (
            self.db.query(ConversationContextCheckpoint)
            .filter(
                ConversationContextCheckpoint.id == checkpoint_id,
                ConversationContextCheckpoint.status == "ready",
                *self._checkpoint_identity_filters(conversation_kind, conversation_id),
            )
            .update(
                {
                    ConversationContextCheckpoint.status: "superseded",
                    ConversationContextCheckpoint.error_code: str(error_code)[:100],
                    ConversationContextCheckpoint.error_detail: str(error_detail)[:4_000],
                    ConversationContextCheckpoint.completed_at: now,
                    ConversationContextCheckpoint.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if superseded != 1:
            raise RuntimeError("active checkpoint was not ready during CAS invalidation")
        self.db.flush()
        self.db.expire(state)
        return True

    def publish_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        last_budget_json: dict[str, Any] | None = None,
    ) -> bool:
        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return False
        checkpoint = self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )
        if checkpoint is None or checkpoint.status != "ready":
            return False
        state = self.ensure_context_state(conversation_kind, conversation_id, owner_id=owner_id)
        if state is None or state.revision != expected_revision:
            return False
        previous_checkpoint_id = state.active_checkpoint_id
        now = datetime.utcnow()
        update_values: dict[Any, Any] = {
            ConversationContextState.active_checkpoint_id: checkpoint.id,
            ConversationContextState.active_source_last_sequence: (checkpoint.source_last_sequence),
            ConversationContextState.revision: expected_revision + 1,
            ConversationContextState.last_compacted_at: now,
            ConversationContextState.updated_at: now,
        }
        if last_budget_json is not None:
            update_values[ConversationContextState.last_budget_json] = last_budget_json
        updated = (
            self.db.query(ConversationContextState)
            .filter(
                ConversationContextState.id == state.id,
                ConversationContextState.revision == expected_revision,
                *self._state_identity_filters(conversation_kind, conversation_id),
            )
            .update(update_values, synchronize_session=False)
        )
        if updated != 1:
            self.db.expire(state)
            return False
        if previous_checkpoint_id and previous_checkpoint_id != checkpoint.id:
            self.db.query(ConversationContextCheckpoint).filter(
                ConversationContextCheckpoint.id == previous_checkpoint_id,
                *self._checkpoint_identity_filters(conversation_kind, conversation_id),
            ).update(
                {
                    ConversationContextCheckpoint.status: "superseded",
                    ConversationContextCheckpoint.completed_at: now,
                    ConversationContextCheckpoint.updated_at: now,
                },
                synchronize_session=False,
            )
        self.db.flush()
        self.db.expire(state)
        return True

    def supersede_inactive_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        error_code: str,
        error_detail: str,
    ) -> bool:
        """Retire one ready CAS loser without racing a late publisher.

        Merely checking that the checkpoint is not active and then changing
        its status leaves a window in which another request can publish that
        same ready row.  Advancing the state revision first is the portable
        cross-process arbiter: a publisher that observed ``expected_revision``
        then loses, while an already-completed publisher makes this CAS fail.
        The active pointer and its source boundary are deliberately preserved.
        """

        if not self._owned_context_conversation(conversation_kind, conversation_id, owner_id):
            return False
        state = self.context_state(
            conversation_kind,
            conversation_id,
            owner_id=owner_id,
        )
        if (
            state is None
            or state.revision != expected_revision
            or str(state.active_checkpoint_id or "") == checkpoint_id
        ):
            return False
        now = datetime.utcnow()
        state_updated = (
            self.db.query(ConversationContextState)
            .filter(
                ConversationContextState.id == state.id,
                ConversationContextState.revision == expected_revision,
                or_(
                    ConversationContextState.active_checkpoint_id.is_(None),
                    ConversationContextState.active_checkpoint_id != checkpoint_id,
                ),
                *self._state_identity_filters(conversation_kind, conversation_id),
            )
            .update(
                {
                    ConversationContextState.revision: expected_revision + 1,
                    ConversationContextState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if state_updated != 1:
            self.db.expire(state)
            return False
        superseded = (
            self.db.query(ConversationContextCheckpoint)
            .filter(
                ConversationContextCheckpoint.id == checkpoint_id,
                ConversationContextCheckpoint.status == "ready",
                *self._checkpoint_identity_filters(conversation_kind, conversation_id),
            )
            .update(
                {
                    ConversationContextCheckpoint.status: "superseded",
                    ConversationContextCheckpoint.error_code: str(error_code)[:100],
                    ConversationContextCheckpoint.error_detail: str(error_detail)[:4_000],
                    ConversationContextCheckpoint.completed_at: now,
                    ConversationContextCheckpoint.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if superseded != 1:
            raise RuntimeError("inactive checkpoint was not ready during CAS supersede")
        self.db.flush()
        self.db.expire(state)
        return True

    def delete_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        checkpoint = self.context_checkpoint(
            conversation_kind,
            conversation_id,
            checkpoint_id,
            owner_id=owner_id,
        )
        if checkpoint is None:
            return False
        state = self.context_state(conversation_kind, conversation_id, owner_id=owner_id)
        if state is not None and state.active_checkpoint_id == checkpoint.id:
            if expected_revision is None or state.revision != expected_revision:
                return False
            updated = (
                self.db.query(ConversationContextState)
                .filter(
                    ConversationContextState.id == state.id,
                    ConversationContextState.revision == expected_revision,
                )
                .update(
                    {
                        ConversationContextState.active_checkpoint_id: None,
                        ConversationContextState.active_source_last_sequence: 0,
                        ConversationContextState.revision: expected_revision + 1,
                        ConversationContextState.updated_at: datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                return False
        self.db.query(ConversationContextCheckpoint).filter(
            ConversationContextCheckpoint.parent_checkpoint_id == checkpoint.id
        ).update(
            {ConversationContextCheckpoint.parent_checkpoint_id: None},
            synchronize_session=False,
        )
        self.db.delete(checkpoint)
        self.db.flush()
        return True

    def conversations_with_counts(self, project_id: str, scope: str):
        conversations = (
            self.db.query(AssistantConversation)
            .filter(
                AssistantConversation.project_id == project_id,
                AssistantConversation.scope == scope,
            )
            .order_by(
                AssistantConversation.updated_at.desc(),
                AssistantConversation.created_at.desc(),
            )
            .all()
        )
        return [
            (
                conversation,
                self.db.query(AssistantMessage)
                .filter(AssistantMessage.conversation_id == conversation.id)
                .count(),
            )
            for conversation in conversations
        ]

    def delete(self, value: Any) -> None:
        self.db.delete(value)

    def runs(self, project_id: str, conversation_id: str | None, *, limit: int):
        query = self.db.query(AssistantRun).filter(AssistantRun.project_id == project_id)
        if conversation_id:
            query = query.filter(AssistantRun.conversation_id == conversation_id)
        return query.order_by(AssistantRun.created_at.desc()).limit(limit).all()

    def conversation_runs(self, project_id: str, conversation_id: str):
        """Return the complete durable run history used by context provenance."""

        return (
            self.db.query(AssistantRun)
            .filter(
                AssistantRun.project_id == project_id,
                AssistantRun.conversation_id == conversation_id,
            )
            .order_by(AssistantRun.created_at.asc(), AssistantRun.id.asc())
            .all()
        )

    def run(self, project_id: str, run_id: str):
        return (
            self.db.query(AssistantRun)
            .filter(
                AssistantRun.project_id == project_id,
                AssistantRun.id == run_id,
            )
            .first()
        )

    def run_steps(self, run_id: str):
        return (
            self.db.query(AssistantRunStep)
            .filter(AssistantRunStep.run_id == run_id)
            .order_by(
                AssistantRunStep.created_at.asc(),
                AssistantRunStep.id.asc(),
            )
            .all()
        )

    def chapter(self, project_id: str, chapter_id: str):
        return (
            self.db.query(Chapter)
            .filter(
                Chapter.id == chapter_id,
                Chapter.project_id == project_id,
            )
            .first()
        )

    def memories(self, project_id: str, categories: Sequence[str], *, limit: int):
        return (
            self.db.query(AssistantMemory)
            .filter(
                AssistantMemory.project_id == project_id,
                AssistantMemory.category.in_(categories),
            )
            .order_by(
                AssistantMemory.importance.desc(),
                AssistantMemory.updated_at.desc(),
            )
            .limit(limit)
            .all()
        )

    def related_memories(
        self,
        project_id: str,
        categories: Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
    ):
        query = self.db.query(AssistantMemory).filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.category.in_(categories),
        )
        for term in terms:
            query = query.filter(
                AssistantMemory.key.ilike(f"%{term}%") | AssistantMemory.value.ilike(f"%{term}%")
            )
        return query.order_by(AssistantMemory.importance.desc()).limit(limit).all()


__all__ = ["SqlAlchemyAssistantWorkspace"]
