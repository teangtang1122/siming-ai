"""Typed contracts for cataloging and narrative-ledger tools."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class ArchiveChapterAfterWriteInput(CompatibleInput):
    chapter_id: str | None = None
    draft_id: str | None = None
    content_ref: str | None = None
    outline_node_id: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    mode: Literal["auto", "manual"] = "auto"
    source: Literal["internal_writer", "local_cli", "external_agent", "repair"] = "internal_writer"
    generate_if_missing: bool = True
    model: str = ""
    context_manifest_id: str | None = None


class InspectStoryGranularityInput(CompatibleInput):
    chapter_id: str | None = None
    level: Literal["basic", "narrative"] = "narrative"
    limit: int = 200


class RepairStoryGranularityInput(CompatibleInput):
    chapter_id: str | None = None
    limit: int = 20
    mode: Literal["manual", "auto"] = "manual"
    repair_level: Literal["basic", "narrative"] = "basic"
    force: bool = False
    model: str = ""


class GetNarrativeLedgerInput(CompatibleInput):
    chapter_id: str | None = None
    types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    storyline: str = ""


_INPUTS: dict[str, type[BaseModel]] = {
    "archive_chapter_after_write": ArchiveChapterAfterWriteInput,
    "inspect_story_granularity": InspectStoryGranularityInput,
    "repair_story_granularity": RepairStoryGranularityInput,
    "get_narrative_ledger": GetNarrativeLedgerInput,
}


def build_continuity_tool_specs(definitions: Mapping[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for name, input_model in _INPUTS.items():
        tool = definitions[name]
        specs.append(
            project_typed_tool_spec(
                tool,
                input_model=input_model,
                version="3.0.0",
            )
        )
    return specs


__all__ = ["build_continuity_tool_specs"]
