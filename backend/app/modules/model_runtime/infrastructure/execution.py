"""Legacy gateway adapter for the model execution application port."""
from __future__ import annotations

from typing import Any

from .gateway import LLMGateway


class GatewayModelExecutor:
    async def chat_completion(self, **kwargs: Any) -> Any:
        return await LLMGateway.chat_completion(**kwargs)

    def stream_chat_completion(self, **kwargs: Any):
        return LLMGateway.stream_chat_completion(**kwargs)

    def stream_chat_completion_with_tools(self, **kwargs: Any):
        return LLMGateway.stream_chat_completion_with_tools(**kwargs)

    def supports_tool_calling(self, model: str | None = None) -> bool:
        return LLMGateway.supports_tool_calling(model)

    def local_cli_extra_body(self, model: str | None = None, **kwargs: Any) -> dict | None:
        return LLMGateway.local_cli_extra_body(model, **kwargs)

    def select_model_for_task(self, **kwargs: Any):
        return LLMGateway.select_model_for_task(**kwargs)

    def model_identity(
        self, model: str | None = None, extra_body: dict | None = None
    ) -> tuple[str, str]:
        return LLMGateway.model_identity(model, extra_body)

    def provider_for_model(self, model: str | None = None) -> str:
        return LLMGateway.provider_for_model(model)


__all__ = ["GatewayModelExecutor"]
