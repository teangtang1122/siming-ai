"""System-assistant conversation application port."""
from __future__ import annotations

from typing import Any, Protocol


class SystemConversationStore(Protocol):
    def list(self) -> dict[str, Any]: ...

    def create(self, title: str) -> dict[str, Any]: ...

    def get(self, conversation_id: str) -> dict[str, Any]: ...

    def append_turn(
        self,
        conversation_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    def delete(self, conversation_id: str) -> dict[str, Any]: ...


__all__ = ["SystemConversationStore"]
