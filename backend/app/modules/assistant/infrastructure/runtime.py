"""Standalone PromptSpec runtime for legacy declarations and quality gates."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..application.prompt_compiler import PromptCompiler
from ..domain.prompt_spec import CompiledPrompt
from .prompt_files import MarkdownPromptRepository

_compiler: PromptCompiler | None = None


def get_default_prompt_compiler() -> PromptCompiler:
    global _compiler
    if _compiler is None:
        _compiler = PromptCompiler(MarkdownPromptRepository())
    return _compiler


def render_prompt(spec_id: str, **values: Any) -> str:
    return get_default_prompt_compiler().render(spec_id, **values)


def get_compiled_prompt(spec_id: str) -> CompiledPrompt:
    return get_default_prompt_compiler().get(spec_id)


def compile_prompt_catalog(
    *,
    known_tools: Iterable[str] | None = None,
) -> Mapping[str, CompiledPrompt]:
    return PromptCompiler(
        MarkdownPromptRepository(),
        known_tools=known_tools,
    ).compile_all()


__all__ = [
    "compile_prompt_catalog",
    "get_compiled_prompt",
    "get_default_prompt_compiler",
    "render_prompt",
]
