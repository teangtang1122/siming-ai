"""Shared operation metadata helpers for novel-creation route groups."""

from __future__ import annotations

from app.ai.local_cli_adapter import is_local_cli_provider
from app.modules.model_runtime.application.execution import model_executor as LLMGateway


def operation_model_identity(model: str | None) -> tuple[str | None, str]:
    effective_model = model
    try:
        selection = LLMGateway.select_model_for_task(
            task_type="planning",
            model_override=model,
        )
        effective_model = selection.model or effective_model
    except Exception:
        pass
    if not effective_model:
        return None, "model_stream"
    try:
        provider, model_name = LLMGateway.model_identity(
            effective_model,
            {"moshu_task_type": "planning"},
        )
        model_label = f"{provider}:{model_name}"
        tool_mode = "local_cli_stream" if is_local_cli_provider(provider) else "api_stream"
        return model_label, tool_mode
    except Exception:
        return effective_model, "model_stream"


__all__ = ["operation_model_identity"]
