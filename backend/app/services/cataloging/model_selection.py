"""Model selection for project cataloging."""
from __future__ import annotations

from ...ai.local_cli_adapter import is_local_cli_provider
from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from ...modules.model_runtime.domain.configuration import TaskModelSelection


def cataloging_model_selection(model_override: str | None = None) -> TaskModelSelection:
    return LLMGateway.select_model_for_task(
        task_type="cataloging",
        model_override=model_override,
    )


def default_cataloging_model(model_override: str | None = None) -> str | None:
    return cataloging_model_selection(model_override).model


def cataloging_extra_body(
    model: str | None,
    *,
    cwd: str | None = None,
    attachments: list[str] | None = None,
) -> dict | None:
    provider = (model or "").split(":", 1)[0].lower()
    base: dict | None = {"moshu_task_type": "cataloging"}
    if provider == "deepseek":
        base["thinking"] = {"type": "disabled"}
    if is_local_cli_provider(provider):
        return LLMGateway.local_cli_extra_body(
            model,
            cwd=cwd,
            attachments=attachments,
            base=base,
        )
    return base
