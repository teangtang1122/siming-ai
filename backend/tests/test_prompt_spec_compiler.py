"""Deterministic PromptSpec compiler and module facade tests."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.modules.assistant.application.prompt_compiler import PromptCompiler
from app.modules.assistant.domain.prompt_spec import PromptSpec
from app.modules.assistant.infrastructure.runtime import compile_prompt_catalog
from app.modules.continuity.interfaces.dependencies import (
    render_external_cataloging_prompt,
    render_merged_cataloging_prompt,
)
from app.modules.creation.interfaces.dependencies import render_creation_prompt
from app.services.workspace.registry import registry


@dataclass
class Repository:
    specs: list[PromptSpec]

    def load_all(self) -> list[PromptSpec]:
        return self.specs


def _spec(spec_id: str, body: str, **metadata) -> PromptSpec:
    return PromptSpec(
        id=spec_id,
        version="1.0.0",
        body=body,
        **metadata,
    )


def test_builtin_prompt_catalog_compiles_against_workspace_tools():
    compiled = compile_prompt_catalog(known_tools=registry.all_names())

    assert set(compiled) == {
        "shared.execution-contract",
        "assistant.workspace.fast",
        "assistant.workspace.quality",
        "assistant.chapter.fast",
        "assistant.chapter.fast.public",
        "assistant.chapter.quality",
        "assistant.chapter.quality.public",
        "creation.novel.stage",
        "continuity.cataloging.merged",
        "continuity.cataloging.external",
    }
    assert len(compiled["assistant.workspace.quality"].template) < 2_000
    assert len(compiled["assistant.chapter.quality"].template) < 1_500
    assert len(compiled["assistant.chapter.fast"].template) < 1_500
    assert compiled["assistant.workspace.fast"].template == compiled["assistant.workspace.quality"].template


def test_compiler_rejects_undeclared_placeholders():
    compiler = PromptCompiler(Repository([_spec("bad", "Hello {name}")]))
    with pytest.raises(ValueError, match="undeclared inputs"):
        compiler.compile_all()


def test_compiler_rejects_fragment_cycles():
    compiler = PromptCompiler(Repository([
        _spec("a", "A", fragments=["b"]),
        _spec("b", "B", fragments=["a"]),
    ]))
    with pytest.raises(ValueError, match="fragment cycle"):
        compiler.compile_all()


def test_compiler_rejects_unknown_tools():
    compiler = PromptCompiler(
        Repository([_spec("bad-tool", "Body", tools=["missing_tool"])]),
        known_tools={"known_tool"},
    )
    with pytest.raises(ValueError, match="unknown tools"):
        compiler.compile_all()


def test_creation_and_continuity_facades_render_compiled_sources():
    creation = render_creation_prompt(task_kind="概念", task_rules="返回三案")
    merged = render_merged_cataloging_prompt()
    external = render_external_cataloging_prompt()

    assert "概念" in creation and "返回三案" in creation
    assert "character_state_update" in merged
    assert "phase=\"merged\"" in external
    assert merged in external
