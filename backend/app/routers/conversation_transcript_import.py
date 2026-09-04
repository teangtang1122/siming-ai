"""Typed, owner-scoped workspace transcript import API."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..architecture.uow import commit_session
from ..core.db_helpers import get_project_or_404
from ..core.exceptions import AppException, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..schemas.conversation_context import TranscriptImportResponse
from ..services.workspace.transcript_import import (
    TranscriptImportCommand,
    TranscriptImportConflictError,
    TranscriptImportMessage,
    TranscriptImportValidationError,
    import_workspace_transcript,
)

router = APIRouter(tags=["ai-writer"])
DbSession = Annotated[Session, Depends(get_db)]


class TranscriptMessageInput(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    sequence_no: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1_000_000)
    status: Literal["completed", "error", "aborted", "cancelled"]
    message_hash: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class TranscriptImportRequest(BaseModel):
    client_conversation_id: str | None = Field(None, min_length=1, max_length=128)
    server_conversation_id: str | None = Field(None, min_length=1, max_length=36)
    transcript_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    title: str | None = Field(None, min_length=1, max_length=200)
    messages: list[TranscriptMessageInput] = Field(min_length=2, max_length=200)


def _device_scope(request: Request) -> str:
    """Use authenticated Gateway identity; never trust a body-supplied device."""

    device_id = str(getattr(request.state, "gateway_device_id", "") or "").strip()
    if device_id:
        return f"gateway:{device_id}"
    return "local-desktop"


def _conflict(message: str) -> AppException:
    return AppException(code=409, message=message, status_code=409)


@router.post(
    "/projects/{project_id}/ai/assistant/conversations/transcript-import",
    response_model=ApiResponse[TranscriptImportResponse],
)
async def import_assistant_conversation_transcript(
    project_id: str,
    payload: TranscriptImportRequest,
    request: Request,
    db: DbSession,
):
    """Import complete closed turns and return the canonical server revision."""

    get_project_or_404(db, project_id)
    command = TranscriptImportCommand(
        project_id=project_id,
        device_scope=_device_scope(request),
        client_conversation_id=payload.client_conversation_id,
        server_conversation_id=payload.server_conversation_id,
        transcript_revision=payload.transcript_revision,
        idempotency_key=payload.idempotency_key,
        title=payload.title,
        messages=tuple(
            TranscriptImportMessage(
                message_id=message.id,
                sequence_no=message.sequence_no,
                role=message.role,
                content=message.content,
                status=message.status,
                message_hash=message.message_hash,
            )
            for message in payload.messages
        ),
    )

    try:
        result = import_workspace_transcript(db, command)
        commit_session(db)
    except TranscriptImportValidationError as exc:
        # Service validation can mention attacker-supplied IDs.  Preserve the
        # typed 400 boundary without reflecting those values or internal
        # validation branches to a remote device.
        raise ValidationError(
            "transcript import payload failed typed and hash validation"
        ) from exc
    except TranscriptImportConflictError as exc:
        # Do not reveal whether a caller guessed an existing conversation,
        # project mapping, device namespace, idempotency key, or revision.
        raise _conflict(
            "transcript import conflicts with the authenticated owner, device, or revision"
        ) from exc
    except IntegrityError as exc:
        # A concurrent request may have won a unique sequence, mapping, or
        # idempotency claim. Never guess which payload won and never overwrite
        # it; the client can retry the same idempotency key for exact recovery.
        db.rollback()
        raise _conflict(
            "transcript changed concurrently; retry the same idempotency key"
        ) from exc

    return ApiResponse.success(
        data={
            "conversation_id": result.conversation_id,
            "transcript_revision": result.transcript_revision,
            "applied_revision": result.applied_revision,
            "imported_message_count": result.imported_message_count,
            "idempotent": result.idempotent,
        },
        message="对话原文增量已同步",
    )


__all__ = [
    "TranscriptImportRequest",
    "TranscriptMessageInput",
    "router",
]
