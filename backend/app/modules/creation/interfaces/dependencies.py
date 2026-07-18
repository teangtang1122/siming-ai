"""Configured creation module dependencies."""
from __future__ import annotations

from ..application.prompting import NovelCreationPromptService

_service: NovelCreationPromptService | None = None


def configure_creation_prompt_service(service: NovelCreationPromptService) -> None:
    global _service
    _service = service


def render_creation_prompt(*, task_kind: str, task_rules: str) -> str:
    if _service is None:
        from ...assistant.interfaces.prompts import get_prompt_compiler

        service = NovelCreationPromptService(get_prompt_compiler())
    else:
        service = _service
    return service.render_stage(task_kind=task_kind, task_rules=task_rules)


__all__ = ["configure_creation_prompt_service", "render_creation_prompt"]
