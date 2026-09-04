"""SQLAlchemy system-assistant conversation adapter."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ....core.exceptions import NotFoundError
from ....core.utils import utc_isoformat
from .models import (
    SystemAssistantConversation,
    SystemAssistantMessage,
    reserve_message_sequence_range,
)


def _title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


def _conversation_data(
    conversation: SystemAssistantConversation,
    message_count: int | None = None,
) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "scope": conversation.scope_type,
        "scope_type": conversation.scope_type,
        "scope_id": conversation.scope_id,
        "project_id": conversation.project_id,
        "message_count": message_count,
        "creation_session_id": conversation.creation_session_id,
        "user_brief": conversation.user_brief,
        "created_at": utc_isoformat(conversation.created_at),
        "updated_at": utc_isoformat(conversation.updated_at),
    }


def _message_data(message: SystemAssistantMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sequence_no": message.sequence_no,
        "role": message.role,
        "content": message.content,
        "run_id": message.run_id,
        "operation_id": message.operation_id,
        "message_type": message.message_type,
        "payload": message.payload_json,
        "status": message.status,
        "created_at": utc_isoformat(message.created_at),
        "updated_at": utc_isoformat(message.updated_at),
    }


class SqlAlchemySystemConversationStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _conversation(self, conversation_id: str) -> SystemAssistantConversation:
        conversation = (
            self._session.query(SystemAssistantConversation)
            .filter(SystemAssistantConversation.id == conversation_id)
            .first()
        )
        if not conversation:
            raise NotFoundError("系统助手对话不存在")
        if conversation.scope_type not in {"creation", "project"}:
            raise NotFoundError("对话不属于当前立项或作品上下文")
        return conversation

    @staticmethod
    def _normalize_scope(scope_type: str, scope_id: str | None) -> tuple[str, str | None]:
        normalized = scope_type.strip().lower()
        if normalized not in {"creation", "project"}:
            raise ValueError("scope_type must be creation or project")
        identifier = (scope_id or "").strip() or None
        if not identifier:
            raise ValueError(f"{normalized} scope requires scope_id")
        return normalized, identifier

    def _apply_scope(
        self,
        conversation: SystemAssistantConversation,
        payload: dict[str, Any],
    ) -> None:
        if payload.get("scope_type") is None and payload.get("scope_id") is None:
            if payload.get("creation_session_id"):
                conversation.scope_type = "creation"
                conversation.scope_id = payload["creation_session_id"]
                conversation.creation_session_id = payload["creation_session_id"]
                conversation.project_id = None
            return
        scope_type, scope_id = self._normalize_scope(
            str(payload.get("scope_type") or conversation.scope_type),
            payload.get("scope_id"),
        )
        conversation.scope_type = scope_type
        conversation.scope_id = scope_id
        conversation.project_id = scope_id if scope_type == "project" else None
        conversation.creation_session_id = scope_id if scope_type == "creation" else None

    def list(self, *, scope_type: str | None = None, scope_id: str | None = None) -> dict[str, Any]:
        query = self._session.query(SystemAssistantConversation).filter(
            SystemAssistantConversation.scope_type.in_(("creation", "project")),
        )
        if scope_type:
            normalized = scope_type.strip().lower()
            if normalized not in {"creation", "project"}:
                raise ValueError("scope_type must be creation or project")
            identifier = (scope_id or "").strip() or None
            query = query.filter(SystemAssistantConversation.scope_type == normalized)
            if identifier:
                query = query.filter(SystemAssistantConversation.scope_id == identifier)
        conversations = (
            query
            .order_by(
                SystemAssistantConversation.updated_at.desc(),
                SystemAssistantConversation.created_at.desc(),
            )
            .all()
        )
        counts = dict(
            self._session.query(
                SystemAssistantMessage.conversation_id,
                func.count(SystemAssistantMessage.id),
            )
            .group_by(SystemAssistantMessage.conversation_id)
            .all()
        )
        items = [
            _conversation_data(conversation, int(counts.get(conversation.id, 0)))
            for conversation in conversations
        ]
        return {"items": items, "total": len(items)}

    def create(
        self,
        title: str,
        *,
        scope_type: str,
        scope_id: str,
    ) -> dict[str, Any]:
        normalized, identifier = self._normalize_scope(scope_type, scope_id)
        conversation = SystemAssistantConversation(
            title=title.strip() or "新对话",
            scope_type=normalized,
            scope_id=identifier,
            project_id=identifier if normalized == "project" else None,
            creation_session_id=identifier if normalized == "creation" else None,
        )
        self._session.add(conversation)
        self._session.flush()
        return {"conversation": _conversation_data(conversation, 0)}

    def set_scope(self, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        self._apply_scope(conversation, payload)
        self._session.flush()
        return {"conversation": _conversation_data(conversation)}

    def get(self, conversation_id: str) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        messages = (
            self._session.query(SystemAssistantMessage)
            .filter(SystemAssistantMessage.conversation_id == conversation.id)
            .order_by(SystemAssistantMessage.sequence_no.asc())
            .all()
        )
        return {
            "conversation": _conversation_data(conversation, len(messages)),
            "messages": [_message_data(message) for message in messages],
        }

    def start_turn(
        self,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a user turn and its running assistant placeholder."""
        conversation = self._conversation(conversation_id)
        if conversation.title == "新对话":
            conversation.title = _title_from_message(str(payload.get("user_content") or ""))
        for field, source in (
            ("creation_session_id", "creation_session_id"),
            ("user_brief", "user_brief"),
        ):
            if payload.get(source) is not None:
                setattr(conversation, field, payload.get(source) or None)
        self._apply_scope(conversation, payload)
        user_sequence, assistant_sequence = reserve_message_sequence_range(
            self._session,
            conversation_model=SystemAssistantConversation,
            message_model=SystemAssistantMessage,
            conversation_id=conversation.id,
            count=2,
        )

        # Starting a newer author turn supersedes every older running
        # placeholder in this append-only conversation.  The older text stays
        # durable as an explicit aborted turn, while its detached producer will
        # observe the status and stop before any post-checkpoint model/tool work.
        older_running = (
            self._session.query(SystemAssistantMessage)
            .filter(
                SystemAssistantMessage.conversation_id == conversation.id,
                SystemAssistantMessage.role == "assistant",
                SystemAssistantMessage.status == "running",
            )
            .all()
        )
        now = datetime.utcnow()
        for older in older_running:
            older.status = "aborted"
            older.content = "本轮已被更新的作者消息替换，未继续执行业务工具。"
            older_payload = (
                dict(older.payload_json) if isinstance(older.payload_json, dict) else {}
            )
            older_payload.update(
                {
                    "superseded": True,
                    "superseded_by_sequence": user_sequence,
                }
            )
            older.payload_json = older_payload
            older.updated_at = now

        user_message = SystemAssistantMessage(
            conversation_id=conversation.id,
            sequence_no=user_sequence,
            role="user",
            content=payload["user_content"],
            status="completed",
        )
        assistant_message = SystemAssistantMessage(
            conversation_id=conversation.id,
            sequence_no=assistant_sequence,
            role="assistant",
            content="",
            run_id=payload.get("run_id"),
            operation_id=payload.get("operation_id"),
            message_type=payload.get("message_type") or "text",
            status="running",
            payload_json=payload.get("payload"),
        )
        self._session.add_all([user_message, assistant_message])
        self._session.flush()
        return {
            "conversation": _conversation_data(conversation),
            "messages": [_message_data(user_message), _message_data(assistant_message)],
        }

    def finish_turn(
        self,
        conversation_id: str,
        assistant_message_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Finish or fail a previously persisted assistant placeholder."""
        conversation = self._conversation(conversation_id)
        message = self._session.query(SystemAssistantMessage).filter(
            SystemAssistantMessage.id == assistant_message_id,
            SystemAssistantMessage.conversation_id == conversation.id,
            SystemAssistantMessage.role == "assistant",
        ).first()
        if not message:
            raise NotFoundError("系统助手消息不存在")
        message.content = payload.get("assistant_content") or ""
        message.status = payload.get("status") or "completed"
        if payload.get("run_id") is not None:
            message.run_id = payload.get("run_id") or None
        if payload.get("operation_id") is not None:
            message.operation_id = payload.get("operation_id") or None
        if payload.get("message_type") is not None:
            message.message_type = payload.get("message_type") or "text"
        incoming_payload = payload.get("payload")
        if isinstance(message.payload_json, dict) and isinstance(incoming_payload, dict):
            # A long-running operation may update its presentation after the
            # Creation Agent has already persisted the model/tool protocol.
            # Merge opaque turn metadata so that status updates cannot erase
            # the durable replay record.
            message.payload_json = {**message.payload_json, **incoming_payload}
        elif incoming_payload is not None or message.payload_json is None:
            message.payload_json = incoming_payload
        if payload.get("creation_session_id") is not None:
            conversation.creation_session_id = payload.get("creation_session_id") or None
        if payload.get("user_brief") is not None:
            conversation.user_brief = payload.get("user_brief") or None
        self._apply_scope(conversation, payload)
        self._session.flush()
        return {"conversation": _conversation_data(conversation), "message": _message_data(message)}

    def interrupt_running_messages(self) -> int:
        """Make abandoned placeholders explicit and recoverable after restart."""
        messages = self._session.query(SystemAssistantMessage).filter(
            SystemAssistantMessage.role == "assistant",
            SystemAssistantMessage.status == "running",
        ).all()
        now = datetime.utcnow()
        for message in messages:
            message.status = "interrupted"
            if not message.content.strip():
                message.content = "上次处理在应用关闭或服务重启时中断，可按原消息重试。"
            payload = dict(message.payload_json or {})
            payload["interrupted_at"] = now.isoformat()
            payload["retryable"] = True
            message.payload_json = payload
            message.updated_at = now
        if messages:
            self._session.flush()
        return len(messages)

    def append_turn(
        self,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        conversation = self._conversation(conversation_id)
        if conversation.title == "新对话":
            conversation.title = _title_from_message(str(payload.get("user_content") or ""))
        if payload.get("creation_session_id") is not None:
            conversation.creation_session_id = payload.get("creation_session_id") or None
        if payload.get("user_brief") is not None:
            conversation.user_brief = payload.get("user_brief") or None
        self._apply_scope(conversation, payload)
        user_sequence, assistant_sequence = reserve_message_sequence_range(
            self._session,
            conversation_model=SystemAssistantConversation,
            message_model=SystemAssistantMessage,
            conversation_id=conversation.id,
            count=2,
        )

        user_message = SystemAssistantMessage(
            conversation_id=conversation.id,
            sequence_no=user_sequence,
            role="user",
            content=payload["user_content"],
            status="completed",
        )
        assistant_message = SystemAssistantMessage(
            conversation_id=conversation.id,
            sequence_no=assistant_sequence,
            role="assistant",
            content=payload.get("assistant_content") or "",
            run_id=payload.get("run_id"),
            operation_id=payload.get("operation_id"),
            message_type=payload.get("message_type") or "text",
            status=payload.get("status") or "completed",
            payload_json=payload.get("payload"),
        )
        self._session.add_all([user_message, assistant_message])
        self._session.flush()
        return {
            "conversation": _conversation_data(conversation),
            "messages": [_message_data(user_message), _message_data(assistant_message)],
        }

    def delete(self, conversation_id: str) -> dict[str, Any]:
        self._session.delete(self._conversation(conversation_id))
        self._session.flush()
        return {"id": conversation_id}


__all__ = ["SqlAlchemySystemConversationStore"]
