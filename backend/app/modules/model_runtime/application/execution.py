"""Model execution port and configured application facade."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol

from ..domain.configuration import TaskModelSelection


class ModelExecutionPort(Protocol):
    async def chat_completion(self, **kwargs: Any) -> Any: ...

    def stream_chat_completion(self, **kwargs: Any) -> AsyncGenerator[str, None]: ...

    def stream_chat_completion_with_tools(
        self, **kwargs: Any
    ) -> AsyncGenerator[dict, None]: ...

    def supports_tool_calling(self, model: str | None = None) -> bool: ...

    def local_cli_extra_body(self, model: str | None = None, **kwargs: Any) -> dict | None: ...

    def select_model_for_task(self, **kwargs: Any) -> TaskModelSelection: ...

    def model_identity(
        self, model: str | None = None, extra_body: dict | None = None
    ) -> tuple[str, str]: ...

    def provider_for_model(self, model: str | None = None) -> str: ...


_executor: ModelExecutionPort | None = None


def configure_model_executor(executor: ModelExecutionPort) -> None:
    global _executor
    _executor = executor


def get_model_executor() -> ModelExecutionPort:
    if _executor is None:
        raise RuntimeError("Model executor has not been configured")
    return _executor


class ModelExecutionFacade:
    """Late-bound facade that keeps module imports free of infrastructure classes."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_model_executor(), name)


model_executor = ModelExecutionFacade()

__all__ = [
    "ModelExecutionPort",
    "configure_model_executor",
    "get_model_executor",
    "model_executor",
]
