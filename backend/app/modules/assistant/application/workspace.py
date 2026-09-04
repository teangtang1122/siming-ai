"""Persistence port used by the workspace assistant controller."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class AssistantWorkspace(Protocol):
    def conversation(self, project_id: str, conversation_id: str) -> Any | None: ...
    def conversation_by_canonical(
        self, project_id: str, canonical_conversation_id: str
    ) -> Any | None: ...
    def create_conversation(self, **values: Any) -> Any: ...
    def create_message(self, **values: Any) -> Any: ...
    def message(self, message_id: str) -> Any | None: ...
    def conversation_messages(self, conversation_id: str) -> Sequence[Any]: ...
    def previous_assistant_messages(self, conversation_id: str) -> Sequence[Any]: ...
    def context_state(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> Any | None: ...
    def ensure_context_state(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> Any | None: ...
    def context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> Any | None: ...
    def context_checkpoints(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> Sequence[Any]: ...
    def create_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        *,
        owner_id: str,
        **values: Any,
    ) -> Any | None: ...
    def add_context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        sources: Sequence[dict[str, Any]],
        *,
        owner_id: str,
    ) -> Sequence[Any] | None: ...
    def context_checkpoint_sources(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
    ) -> Sequence[Any]: ...
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
    ) -> Any | None: ...
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
    ) -> bool: ...
    def publish_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        expected_revision: int,
        *,
        owner_id: str,
        last_budget_json: dict[str, Any] | None = None,
    ) -> bool: ...
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
    ) -> bool: ...
    def delete_context_checkpoint(
        self,
        conversation_kind: str,
        conversation_id: str,
        checkpoint_id: str,
        *,
        owner_id: str,
        expected_revision: int | None = None,
    ) -> bool: ...
    def conversations_with_counts(
        self,
        project_id: str,
        scope: str,
    ) -> Sequence[tuple[Any, int]]: ...
    def delete(self, value: Any) -> None: ...
    def runs(
        self,
        project_id: str,
        conversation_id: str | None,
        *,
        limit: int,
    ) -> Sequence[Any]: ...
    def conversation_runs(
        self,
        project_id: str,
        conversation_id: str,
    ) -> Sequence[Any]: ...
    def run(self, project_id: str, run_id: str) -> Any | None: ...
    def run_steps(self, run_id: str) -> Sequence[Any]: ...
    def chapter(self, project_id: str, chapter_id: str) -> Any | None: ...
    def memories(
        self,
        project_id: str,
        categories: Sequence[str],
        *,
        limit: int,
    ) -> Sequence[Any]: ...
    def related_memories(
        self,
        project_id: str,
        categories: Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
    ) -> Sequence[Any]: ...


__all__ = ["AssistantWorkspace"]
