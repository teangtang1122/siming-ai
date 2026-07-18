"""Configured PromptSpec access for legacy and modular callers."""
from __future__ import annotations

from typing import Any

from ..application.prompt_compiler import PromptCompiler
from ..domain.prompt_spec import CompiledPrompt

_compiler: PromptCompiler | None = None


def configure_prompt_compiler(compiler: PromptCompiler) -> None:
    global _compiler
    compiler.compile_all()
    _compiler = compiler


def get_prompt_compiler() -> PromptCompiler:
    if _compiler is None:
        raise RuntimeError("Prompt compiler has not been configured")
    return _compiler


def render_prompt(spec_id: str, **values: Any) -> str:
    return get_prompt_compiler().render(spec_id, **values)


def get_compiled_prompt(spec_id: str) -> CompiledPrompt:
    return get_prompt_compiler().get(spec_id)


__all__ = [
    "configure_prompt_compiler",
    "get_compiled_prompt",
    "get_prompt_compiler",
    "render_prompt",
]
