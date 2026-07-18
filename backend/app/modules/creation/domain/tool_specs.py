"""Typed contracts for new-novel workspace tools."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class StartNovelCreationSessionInput(CompatibleInput):
    mode: Literal["internal_llm", "external_agent"] = "external_agent"
    user_brief: str = ""
    target_audience: str = ""
    genre: str = ""
    platform: str = ""


class DraftNovelBlueprintInput(CompatibleInput):
    session_id: str
    execution_mode: Literal["template", "hybrid", "internal_llm", "external_agent"] = "template"
    user_brief: str = ""
    feedback: str = ""
    revision_mode: Literal["initial", "refine", "regenerate"] = "initial"
    enhance_with_llm: bool = False
    skip_questions: bool = False
    depth: Literal["concept", "full"] = "full"


class ApplyNovelBlueprintInput(CompatibleInput):
    session_id: str
    blueprint_index: int = 0
    mode: Literal["manual", "auto"] = "manual"
    blueprint: dict[str, Any] = Field(default_factory=dict)


class GetNovelCreationSessionInput(CompatibleInput):
    session_id: str


class GenerateNovelCreationStageInput(CompatibleInput):
    session_id: str
    stage: str
    model: str = ""
    use_model: bool = True
    auto_confirm: bool = False
    session_patch: dict[str, Any] = Field(default_factory=dict)


class SubmitNovelCreationStageInput(CompatibleInput):
    session_id: str
    stage: str
    data: dict[str, Any]
    confirm: bool = False
    source: str = "external_agent"


_INPUTS: dict[str, type[BaseModel]] = {
    "start_novel_creation_session": StartNovelCreationSessionInput,
    "draft_novel_blueprint": DraftNovelBlueprintInput,
    "apply_novel_blueprint": ApplyNovelBlueprintInput,
    "get_novel_creation_session": GetNovelCreationSessionInput,
    "generate_novel_creation_stage": GenerateNovelCreationStageInput,
    "submit_novel_creation_stage": SubmitNovelCreationStageInput,
}


def build_creation_tool_specs(definitions: Mapping[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for name, input_model in _INPUTS.items():
        tool = definitions[name]
        specs.append(
            project_typed_tool_spec(
                tool,
                input_model=input_model,
                version="3.0.0-beta.1",
            )
        )
    return specs


__all__ = ["build_creation_tool_specs"]
