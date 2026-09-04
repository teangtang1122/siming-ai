"""Typed contracts for cataloging and narrative-ledger tools."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec
from ...story.interfaces.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from .cataloging_contract import CatalogingFactType


class CompatibleInput(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


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


class CatalogingFactInput(CompatibleInput):
    fact_type: CatalogingFactType
    payload: dict[str, Any]
    evidence: str | None = None
    confidence: float | None = None


class SaveExternalCatalogingFactsInput(CompatibleInput):
    job_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    facts: list[CatalogingFactInput]


class SaveExternalCatalogingCandidatesInput(CompatibleInput):
    job_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    candidates: list[dict[str, Any]]


class OutlineProposalNodeInput(CompatibleInput):
    title: str = Field(min_length=1, max_length=200)
    node_type: Literal["volume", "chapter", "section"] = "chapter"
    summary: str
    character_names: list[str] = Field(
        default_factory=list,
        description=(
            "Characters involved in this future plan. Names that do not yet have a character "
            "record remain unlinked planning metadata when the author confirms the draft; "
            "confirmation never creates placeholder character records."
        ),
    )
    parent_title: str | None = Field(
        default=None,
        description=(
            "Optional parent title. Use a title from this same proposal for nested nodes. "
            "Top-level nodes may omit it; a value matching the formal parent_id is accepted "
            "and normalized."
        ),
    )


class SaveExternalOutlineDraftInput(CompatibleInput):
    context_manifest_id: str = Field(min_length=1)
    context_selection_token: str = Field(min_length=1)
    parent_id: str | None = None
    insert_after_id: str | None = None
    nodes: list[OutlineProposalNodeInput] = Field(
        min_length=1, max_length=OUTLINE_PROPOSAL_MAX_NODES,
        description=(
            "Native node array; length must equal the prepared outline_planning "
            "batch_count. summary describes future plans, not actual events."
        ),
    )
    design_notes: str = ""


_INPUTS: dict[str, type[BaseModel]] = {
    "inspect_story_granularity": InspectStoryGranularityInput,
    "repair_story_granularity": RepairStoryGranularityInput,
    "get_narrative_ledger": GetNarrativeLedgerInput,
    "save_external_cataloging_facts": SaveExternalCatalogingFactsInput,
    "save_external_cataloging_candidates": SaveExternalCatalogingCandidatesInput,
    "save_external_outline_draft": SaveExternalOutlineDraftInput,
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
