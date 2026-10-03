"""Typed contracts for cataloging and narrative-ledger tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ....architecture.tool_spec import ToolSpec, project_typed_tool_spec
from ...story.interfaces.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from .candidate_contract import candidate_generation_schema, candidate_record_schema


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


class SaveExternalCatalogingCandidatesInput(CompatibleInput):
    job_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    finalize: bool = Field(
        default=False,
        description=(
            "Set true only when this chapter plan is complete; "
            "schema/coverage must pass before apply."
        ),
    )
    reject_candidate_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit IDs of unedited, unapplied candidates in this chapter "
            "that the Agent retracts while correcting its plan."
        ),
    )
    candidates: list[dict[str, Any]] = Field(
        description=(
            "Native JSON candidate objects with a canonical type and fields at the same level. "
            "Never encode objects/arrays as strings. A chapter_link is one aggregate record, "
            "not one record per entity. Submit summary plan before dependent records. "
            "After rejection, correct candidate_errors using recovery_context; "
            "do not resend accepted records."
        ),
        json_schema_extra={"items": candidate_generation_schema()},
    )


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
        min_length=1,
        max_length=OUTLINE_PROPOSAL_MAX_NODES,
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
    "save_external_cataloging_candidates": SaveExternalCatalogingCandidatesInput,
    "save_external_outline_draft": SaveExternalOutlineDraftInput,
}


def build_continuity_tool_specs(definitions: Mapping[str, Any]) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for name, input_model in _INPUTS.items():
        tool = definitions[name]
        spec = project_typed_tool_spec(
            tool,
            input_model=input_model,
            version="3.0.0",
        )
        if name == "save_external_cataloging_candidates":
            strict_schema = spec.parameters_schema()
            strict_schema["properties"]["candidates"]["items"] = candidate_record_schema()
            spec = replace(spec, input_validation_schema_override=strict_schema)
        specs.append(spec)
    return specs


__all__ = ["build_continuity_tool_specs"]
