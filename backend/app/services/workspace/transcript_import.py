"""Explicit incremental import for a device-owned workspace transcript.

This is the only bridge from a standalone client transcript to the canonical
``AssistantConversation`` store. It accepts typed, hashed message records; it
never accepts an untyped ``history`` array and never rewrites an existing
message. The caller owns the transaction and commits only after this service
returns successfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models_support import generate_uuid
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantConversationReplica,
    AssistantMessage,
    AssistantTranscriptImportReceipt,
    SystemAssistantConversation,
    SystemAssistantMessage,
    reserve_message_sequence_range,
)
from app.modules.story.infrastructure.entities import Project
from app.services.conversation_context.canonical import canonical_sha256

_MESSAGE_SCHEMA = "assistant_transcript_message.v1"
_IMPORT_SCHEMA = "assistant_transcript_import.v1"
_TRANSCRIPT_TITLE_TRIM_CHARS = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
_ROLES = {"user", "assistant"}
_CLOSED_ASSISTANT_STATUSES = {"completed", "error", "aborted", "cancelled"}
_OWNERSHIP_CONFLICT = (
    "transcript mapping does not exist for the authenticated owner and device"
)


class TranscriptImportValidationError(ValueError):
    """The typed import payload is internally invalid."""


class TranscriptImportConflictError(RuntimeError):
    """The request conflicts with durable ownership or transcript data."""


@dataclass(frozen=True)
class TranscriptImportMessage:
    message_id: str
    sequence_no: int
    role: Literal["user", "assistant"]
    content: str
    status: Literal["completed", "error", "aborted", "cancelled"]
    message_hash: str

    def canonical_record(self) -> dict[str, object]:
        return {
            "schema": _MESSAGE_SCHEMA,
            "id": self.message_id,
            "sequence_no": self.sequence_no,
            "role": self.role,
            "content": self.content,
            "status": self.status,
        }


@dataclass(frozen=True)
class TranscriptImportCommand:
    project_id: str
    device_scope: str
    transcript_revision: int
    idempotency_key: str
    messages: tuple[TranscriptImportMessage, ...]
    client_conversation_id: str | None = None
    server_conversation_id: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class TranscriptImportResult:
    conversation_id: str
    transcript_revision: int
    applied_revision: int
    imported_message_count: int
    idempotent: bool


@dataclass(frozen=True)
class SystemConversationImportResult:
    conversation: AssistantConversation
    transcript_revision: int
    imported_message_count: int
    created: bool


def transcript_message_hash(
    *,
    message_id: str,
    sequence_no: int,
    role: str,
    content: str,
    status: str,
) -> str:
    """Return the cross-client hash for one exact typed message record."""

    return canonical_sha256(
        {
            "schema": _MESSAGE_SCHEMA,
            "id": message_id,
            "sequence_no": sequence_no,
            "role": role,
            "content": content,
            "status": status,
        }
    )


def _required_string(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranscriptImportValidationError(f"{field} must not be empty")
    if len(value) > maximum:
        raise TranscriptImportValidationError(f"{field} is too long")
    return value.strip()


def _optional_string(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_string(value, field, maximum=maximum)


def normalize_transcript_import_title(value: str | None) -> str | None:
    """Canonicalize the optional cross-client title before hashing and storage."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise TranscriptImportValidationError("title must be a string")
    normalized = value.strip(_TRANSCRIPT_TITLE_TRIM_CHARS).replace("\r\n", " ")
    for line_break in ("\r", "\n", "\u0085", "\u2028", "\u2029"):
        normalized = normalized.replace(line_break, " ")
    if not normalized:
        raise TranscriptImportValidationError("title must not be empty")
    if len(normalized) > 200:
        raise TranscriptImportValidationError("title is too long")
    return normalized


def _validate_messages(command: TranscriptImportCommand) -> tuple[TranscriptImportMessage, ...]:
    messages = tuple(command.messages)
    if not messages or len(messages) % 2:
        raise TranscriptImportValidationError(
            "transcript import must contain one or more complete user/assistant turns"
        )

    seen_ids: set[str] = set()
    expected_sequence: int | None = None
    for index, message in enumerate(messages):
        message_id = _required_string(message.message_id, "message.id", maximum=36)
        if message_id != message.message_id:
            raise TranscriptImportValidationError("message.id must not contain outer whitespace")
        if message_id in seen_ids:
            raise TranscriptImportValidationError("message IDs must be unique within an import")
        seen_ids.add(message_id)
        if isinstance(message.sequence_no, bool) or message.sequence_no <= 0:
            raise TranscriptImportValidationError("message sequence_no must be positive")
        if expected_sequence is not None and message.sequence_no != expected_sequence:
            raise TranscriptImportValidationError("message sequence_no values must be contiguous")
        expected_sequence = message.sequence_no + 1
        expected_role = "user" if index % 2 == 0 else "assistant"
        if message.role != expected_role or message.role not in _ROLES:
            raise TranscriptImportValidationError(
                "transcript import must preserve consecutive user/assistant role order"
            )
        if not isinstance(message.content, str) or not message.content.strip():
            raise TranscriptImportValidationError("message content must be preserved and non-empty")
        if message.role == "user" and message.status != "completed":
            raise TranscriptImportValidationError(
                "a closed imported turn must preserve its user message as completed"
            )
        if message.role == "assistant" and message.status not in _CLOSED_ASSISTANT_STATUSES:
            raise TranscriptImportValidationError("assistant message is not in a closed state")
        expected_hash = transcript_message_hash(
            message_id=message.message_id,
            sequence_no=message.sequence_no,
            role=message.role,
            content=message.content,
            status=message.status,
        )
        if message.message_hash.lower() != expected_hash:
            raise TranscriptImportValidationError(
                f"message {message.message_id} hash does not match its exact typed record"
            )

    if messages[0].sequence_no % 2 != 1:
        raise TranscriptImportValidationError("a transcript batch must begin at a user sequence")
    if command.transcript_revision != messages[-1].sequence_no:
        raise TranscriptImportValidationError(
            "transcript_revision must equal the final imported message sequence"
        )
    return messages


def _request_hash(command: TranscriptImportCommand) -> str:
    return canonical_sha256(
        {
            "schema": _IMPORT_SCHEMA,
            "project_id": command.project_id,
            "client_conversation_id": command.client_conversation_id,
            "server_conversation_id": command.server_conversation_id,
            "transcript_revision": command.transcript_revision,
            "idempotency_key": command.idempotency_key,
            "title": command.title,
            "messages": [message.canonical_record() for message in command.messages],
        }
    )


def _conversation_revision(db: Session, conversation_id: str) -> int:
    value = db.execute(
        select(func.max(AssistantMessage.sequence_no)).where(
            AssistantMessage.conversation_id == conversation_id
        )
    ).scalar_one()
    return int(value or 0)


def _replay_import_receipt(
    db: Session,
    *,
    prior_receipt: AssistantTranscriptImportReceipt,
    command: TranscriptImportCommand,
    request_hash: str,
) -> TranscriptImportResult:
    """Replay only through an exact project/conversation/replica/device owner join."""

    owned = db.execute(
        select(
            AssistantTranscriptImportReceipt,
            AssistantConversationReplica,
            AssistantConversation,
        )
        .select_from(AssistantTranscriptImportReceipt)
        .join(
            AssistantConversationReplica,
            AssistantConversationReplica.id
            == AssistantTranscriptImportReceipt.replica_id,
        )
        .join(
            AssistantConversation,
            AssistantConversation.id
            == AssistantTranscriptImportReceipt.assistant_conversation_id,
        )
        .join(Project, Project.id == AssistantTranscriptImportReceipt.project_id)
        .where(
            AssistantTranscriptImportReceipt.id == prior_receipt.id,
            AssistantTranscriptImportReceipt.project_id == command.project_id,
            AssistantTranscriptImportReceipt.device_scope == command.device_scope,
            AssistantConversationReplica.project_id == command.project_id,
            AssistantConversationReplica.device_scope == command.device_scope,
            AssistantConversationReplica.assistant_conversation_id
            == AssistantTranscriptImportReceipt.assistant_conversation_id,
            AssistantConversation.project_id == command.project_id,
            Project.id == command.project_id,
        )
        .with_for_update()
    ).one_or_none()
    if owned is None:
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
    receipt, replica, conversation = owned
    if command.server_conversation_id and command.server_conversation_id != conversation.id:
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
    if (
        command.client_conversation_id
        and command.client_conversation_id != replica.client_conversation_id
    ):
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
    if receipt.request_hash != request_hash:
        raise TranscriptImportConflictError(
            "idempotency key was already used for a different transcript import"
        )
    current_revision = _conversation_revision(db, conversation.id)
    if current_revision < receipt.result_transcript_revision:
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
    return TranscriptImportResult(
        conversation_id=conversation.id,
        transcript_revision=current_revision,
        applied_revision=receipt.result_transcript_revision,
        imported_message_count=receipt.imported_message_count,
        idempotent=True,
    )


def _assert_replica_owner(
    replica: AssistantConversationReplica,
    *,
    project_id: str,
    device_scope: str,
) -> None:
    if replica.project_id != project_id or replica.device_scope != device_scope:
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)


def _resolve_replica(
    db: Session,
    command: TranscriptImportCommand,
) -> tuple[AssistantConversationReplica, AssistantConversation, bool]:
    by_server: AssistantConversationReplica | None = None
    if command.server_conversation_id:
        by_server = db.execute(
            select(AssistantConversationReplica)
            .where(
                AssistantConversationReplica.assistant_conversation_id
                == command.server_conversation_id
            )
            .with_for_update()
        ).scalar_one_or_none()
        if by_server is None:
            raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
        _assert_replica_owner(
            by_server,
            project_id=command.project_id,
            device_scope=command.device_scope,
        )

    by_client: AssistantConversationReplica | None = None
    if command.client_conversation_id:
        by_client = db.execute(
            select(AssistantConversationReplica)
            .where(
                AssistantConversationReplica.device_scope == command.device_scope,
                AssistantConversationReplica.client_conversation_id
                == command.client_conversation_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if by_client is not None:
            _assert_replica_owner(
                by_client,
                project_id=command.project_id,
                device_scope=command.device_scope,
            )

    if by_server is not None and by_client is not None and by_server.id != by_client.id:
        raise TranscriptImportConflictError(
            "client and server conversation IDs resolve to different transcripts"
        )
    replica = by_server or by_client
    if replica is not None:
        if (
            command.client_conversation_id
            and replica.client_conversation_id != command.client_conversation_id
        ):
            raise TranscriptImportConflictError(
                "client conversation ID does not match the server transcript mapping"
            )
        conversation = db.execute(
            select(AssistantConversation)
            .where(
                AssistantConversation.id == replica.assistant_conversation_id,
                AssistantConversation.project_id == command.project_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if conversation is None:
            raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
        return replica, conversation, False

    if command.server_conversation_id:
        raise TranscriptImportConflictError(_OWNERSHIP_CONFLICT)
    if command.messages[0].sequence_no != 1:
        raise TranscriptImportConflictError(
            "a new server transcript must begin with message sequence 1"
        )
    title = command.title or command.messages[0].content.strip(
        _TRANSCRIPT_TITLE_TRIM_CHARS
    ).replace("\r\n", " ")
    for line_break in ("\r", "\n", "\u0085", "\u2028", "\u2029"):
        title = title.replace(line_break, " ")
    conversation = AssistantConversation(
        id=generate_uuid(),
        project_id=command.project_id,
        title=(title[:200] or "新对话"),
        scope="project",
    )
    replica = AssistantConversationReplica(
        id=generate_uuid(),
        project_id=command.project_id,
        assistant_conversation_id=conversation.id,
        device_scope=command.device_scope,
        client_conversation_id=command.client_conversation_id,
    )
    db.add_all([conversation, replica])
    db.flush()
    return replica, conversation, True


def _validate_existing_transcript(
    records: tuple[AssistantMessage, ...],
    *,
    conversation_id: str,
) -> None:
    if len(records) % 2:
        raise TranscriptImportConflictError("server transcript contains an incomplete turn")
    for index, record in enumerate(records):
        expected_sequence = index + 1
        expected_role = "user" if index % 2 == 0 else "assistant"
        if record.conversation_id != conversation_id or record.sequence_no != expected_sequence:
            raise TranscriptImportConflictError("server transcript sequence is not contiguous")
        if record.role != expected_role:
            raise TranscriptImportConflictError("server transcript role order is invalid")
        if expected_role == "user" and record.status != "completed":
            raise TranscriptImportConflictError("server transcript contains an unclosed user task")
        if expected_role == "assistant" and record.status not in _CLOSED_ASSISTANT_STATUSES:
            raise TranscriptImportConflictError("server transcript contains an open assistant turn")
        if not isinstance(record.content, str) or not record.content.strip():
            raise TranscriptImportConflictError("server transcript contains an empty message")


def _same_message(record: AssistantMessage, incoming: TranscriptImportMessage) -> bool:
    return all(
        (
            record.id == incoming.message_id,
            record.sequence_no == incoming.sequence_no,
            record.role == incoming.role,
            record.content == incoming.content,
            record.status == incoming.status,
        )
    )


def _normalize_import_command(command: TranscriptImportCommand) -> TranscriptImportCommand:
    project_id = _required_string(command.project_id, "project_id", maximum=36)
    device_scope = _required_string(command.device_scope, "device_scope", maximum=128)
    idempotency_key = _required_string(
        command.idempotency_key,
        "idempotency_key",
        maximum=200,
    )
    client_conversation_id = _optional_string(
        command.client_conversation_id,
        "client_conversation_id",
        maximum=128,
    )
    server_conversation_id = _optional_string(
        command.server_conversation_id,
        "server_conversation_id",
        maximum=36,
    )
    if isinstance(command.transcript_revision, bool) or command.transcript_revision <= 0:
        raise TranscriptImportValidationError("transcript_revision must be positive")
    return TranscriptImportCommand(
        project_id=project_id,
        device_scope=device_scope,
        transcript_revision=command.transcript_revision,
        idempotency_key=idempotency_key,
        messages=tuple(command.messages),
        client_conversation_id=client_conversation_id,
        server_conversation_id=server_conversation_id,
        title=normalize_transcript_import_title(command.title),
    )


def _new_import_messages(
    db: Session,
    *,
    conversation: AssistantConversation,
    messages: tuple[TranscriptImportMessage, ...],
    existing_records: tuple[AssistantMessage, ...],
) -> list[TranscriptImportMessage]:
    current_revision = len(existing_records)
    existing_by_sequence = {record.sequence_no: record for record in existing_records}
    new_messages: list[TranscriptImportMessage] = []
    for incoming in messages:
        globally_existing = db.get(AssistantMessage, incoming.message_id)
        sequence_existing = existing_by_sequence.get(incoming.sequence_no)
        if globally_existing is not None:
            if globally_existing.conversation_id != conversation.id or not _same_message(
                globally_existing,
                incoming,
            ):
                raise TranscriptImportConflictError(
                    "message ID already belongs to different content or ownership"
                )
            if sequence_existing is None or sequence_existing.id != globally_existing.id:
                raise TranscriptImportConflictError("message sequence ownership conflicts")
            continue
        if sequence_existing is not None:
            raise TranscriptImportConflictError(
                "message sequence already contains a different stable message ID"
            )
        if incoming.sequence_no <= current_revision:
            raise TranscriptImportConflictError(
                "imported message is missing from its claimed prefix"
            )
        new_messages.append(incoming)
    return new_messages


def import_workspace_transcript(
    db: Session,
    command: TranscriptImportCommand,
) -> TranscriptImportResult:
    """Validate and atomically stage one exact transcript increment."""

    normalized = _normalize_import_command(command)
    project_id = normalized.project_id
    device_scope = normalized.device_scope
    idempotency_key = normalized.idempotency_key
    messages = _validate_messages(normalized)
    request_hash = _request_hash(normalized)

    prior_receipt = db.execute(
        select(AssistantTranscriptImportReceipt)
        .where(
            AssistantTranscriptImportReceipt.device_scope == device_scope,
            AssistantTranscriptImportReceipt.idempotency_key == idempotency_key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if prior_receipt is not None:
        return _replay_import_receipt(
            db,
            prior_receipt=prior_receipt,
            command=normalized,
            request_hash=request_hash,
        )

    replica, conversation, _created = _resolve_replica(db, normalized)
    existing_records = tuple(
        db.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.sequence_no.asc())
        ).scalars()
    )
    _validate_existing_transcript(existing_records, conversation_id=conversation.id)
    current_revision = len(existing_records)
    new_messages = _new_import_messages(
        db,
        conversation=conversation,
        messages=messages,
        existing_records=existing_records,
    )

    if new_messages:
        if new_messages[0].sequence_no != current_revision + 1:
            raise TranscriptImportConflictError(
                "transcript import cannot leave a gap after the server revision"
            )
        reserved = reserve_message_sequence_range(
            db,
            conversation_model=AssistantConversation,
            message_model=AssistantMessage,
            conversation_id=conversation.id,
            count=len(new_messages),
        )
        incoming_sequences = tuple(message.sequence_no for message in new_messages)
        if reserved != incoming_sequences:
            raise TranscriptImportConflictError(
                "server transcript changed while the increment was being imported"
            )
        now = datetime.utcnow()
        db.add_all(
            AssistantMessage(
                id=message.message_id,
                conversation_id=conversation.id,
                sequence_no=message.sequence_no,
                role=message.role,
                content=message.content,
                status=message.status,
                created_at=now,
                updated_at=now,
            )
            for message in new_messages
        )
        current_revision = reserved[-1]
        conversation.updated_at = now

    if current_revision < normalized.transcript_revision:
        raise TranscriptImportConflictError(
            "source transcript revision is ahead of the imported complete increment"
        )
    receipt = AssistantTranscriptImportReceipt(
        id=generate_uuid(),
        project_id=project_id,
        assistant_conversation_id=conversation.id,
        replica_id=replica.id,
        device_scope=device_scope,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        source_transcript_revision=normalized.transcript_revision,
        source_first_sequence=messages[0].sequence_no,
        source_last_sequence=messages[-1].sequence_no,
        source_message_count=len(messages),
        imported_message_count=len(new_messages),
        result_transcript_revision=current_revision,
    )
    db.add(receipt)
    db.flush()
    return TranscriptImportResult(
        conversation_id=conversation.id,
        transcript_revision=current_revision,
        applied_revision=current_revision,
        imported_message_count=len(new_messages),
        idempotent=not new_messages,
    )


def _closed_system_message_prefix(
    source_messages: tuple[SystemAssistantMessage, ...],
) -> tuple[SystemAssistantMessage, ...]:
    if len(source_messages) % 2:
        raise TranscriptImportConflictError(
            "canonical System conversation contains an incomplete stored turn"
        )
    closed_prefix: list[SystemAssistantMessage] = []
    for index in range(0, len(source_messages), 2):
        user = source_messages[index]
        assistant = source_messages[index + 1]
        expected_user_sequence = index + 1
        if (
            user.sequence_no != expected_user_sequence
            or assistant.sequence_no != expected_user_sequence + 1
            or user.role != "user"
            or assistant.role != "assistant"
            or user.status != "completed"
            or not isinstance(user.content, str)
            or not user.content.strip()
        ):
            raise TranscriptImportConflictError(
                "canonical System conversation has invalid typed turn order"
            )
        if assistant.status == "running":
            if index != len(source_messages) - 2:
                raise TranscriptImportConflictError(
                    "canonical System conversation has an open historical turn"
                )
            break
        if (
            assistant.status not in _CLOSED_ASSISTANT_STATUSES
            or not isinstance(assistant.content, str)
            or not assistant.content.strip()
        ):
            raise TranscriptImportConflictError(
                "canonical System conversation has an unsupported closed turn"
            )
        closed_prefix.extend((user, assistant))
    return tuple(closed_prefix)


def _typed_system_import_messages(
    closed_prefix: tuple[SystemAssistantMessage, ...],
) -> tuple[TranscriptImportMessage, ...]:
    return tuple(
        TranscriptImportMessage(
            message_id=str(message.id),
            sequence_no=int(message.sequence_no),
            role=str(message.role),  # type: ignore[arg-type]
            content=str(message.content),
            status=str(message.status),  # type: ignore[arg-type]
            message_hash=transcript_message_hash(
                message_id=str(message.id),
                sequence_no=int(message.sequence_no),
                role=str(message.role),
                content=str(message.content),
                status=str(message.status),
            ),
        )
        for message in closed_prefix
    )


def ensure_workspace_transcript_from_system_conversation(
    db: Session,
    *,
    project_id: str,
    system_conversation_id: str,
) -> SystemConversationImportResult:
    """Create the PC execution conversation from its canonical visible source.

    The System Assistant UI persists the current user plus a running assistant
    placeholder before invoking the workspace Agent. This adapter imports only
    the preceding complete closed turns. The current turn is then appended by
    the workspace controller with its own durable IDs. On later calls the
    already-mapped ``AssistantConversation`` is authoritative, so we do not
    replay the System transcript under a second set of message IDs.

    The caller should invoke this in place of the current
    ``conversation_by_canonical/create_conversation`` block and commit before
    starting the model run.
    """

    owner_id = _required_string(project_id, "project_id", maximum=36)
    source_id = _required_string(
        system_conversation_id,
        "system_conversation_id",
        maximum=36,
    )
    source = db.execute(
        select(SystemAssistantConversation).where(
            SystemAssistantConversation.id == source_id
        )
    ).scalar_one_or_none()
    if (
        source is None
        or source.scope_type != "project"
        or source.scope_id != owner_id
        or source.project_id != owner_id
    ):
        raise TranscriptImportConflictError(
            "canonical System conversation does not belong to this project"
        )

    existing = db.execute(
        select(AssistantConversation)
        .where(
            AssistantConversation.project_id == owner_id,
            AssistantConversation.canonical_conversation_id == source_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        return SystemConversationImportResult(
            conversation=existing,
            transcript_revision=_conversation_revision(db, existing.id),
            imported_message_count=0,
            created=False,
        )

    source_messages = tuple(
        db.execute(
            select(SystemAssistantMessage)
            .where(SystemAssistantMessage.conversation_id == source_id)
            .order_by(SystemAssistantMessage.sequence_no.asc())
        ).scalars()
    )
    closed_prefix = _closed_system_message_prefix(source_messages)

    conversation = AssistantConversation(
        id=generate_uuid(),
        project_id=owner_id,
        title=str(source.title or "新对话")[:200],
        scope="project",
        canonical_conversation_id=source_id,
    )
    device_scope = f"canonical-system:{source_id}"
    replica = AssistantConversationReplica(
        id=generate_uuid(),
        project_id=owner_id,
        assistant_conversation_id=conversation.id,
        device_scope=device_scope,
        client_conversation_id=source_id,
    )
    db.add_all([conversation, replica])
    db.flush()

    if not closed_prefix:
        return SystemConversationImportResult(
            conversation=conversation,
            transcript_revision=0,
            imported_message_count=0,
            created=True,
        )

    typed = _typed_system_import_messages(closed_prefix)
    result = import_workspace_transcript(
        db,
        TranscriptImportCommand(
            project_id=owner_id,
            device_scope=device_scope,
            client_conversation_id=source_id,
            server_conversation_id=conversation.id,
            transcript_revision=typed[-1].sequence_no,
            idempotency_key=canonical_sha256(
                {
                    "schema": "system_conversation_import.v1",
                    "system_conversation_id": source_id,
                    "last_sequence": typed[-1].sequence_no,
                    "message_hashes": [message.message_hash for message in typed],
                }
            ),
            title=str(source.title or "新对话"),
            messages=typed,
        ),
    )
    return SystemConversationImportResult(
        conversation=conversation,
        transcript_revision=result.transcript_revision,
        imported_message_count=result.imported_message_count,
        created=True,
    )


__all__ = [
    "TranscriptImportCommand",
    "TranscriptImportConflictError",
    "TranscriptImportMessage",
    "TranscriptImportResult",
    "TranscriptImportValidationError",
    "SystemConversationImportResult",
    "ensure_workspace_transcript_from_system_conversation",
    "import_workspace_transcript",
    "normalize_transcript_import_title",
    "transcript_message_hash",
]
