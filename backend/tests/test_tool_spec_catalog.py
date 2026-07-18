"""Typed workspace tool catalog compatibility tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.workspace.registry import registry


def _openai_parameters(name: str) -> dict:
    schema = next(
        item for item in registry.get_schemas() if item["function"]["name"] == name
    )
    return schema["function"]["parameters"]


def test_creation_tool_schema_has_one_typed_source():
    spec = registry.get_spec("generate_novel_creation_stage")
    assert spec is not None
    assert spec.version == "3.0.0"
    assert _openai_parameters(spec.name) == spec.parameters_schema()
    assert spec.mcp_schema()["inputSchema"] == spec.parameters_schema()
    assert spec.frontend_metadata()["version"] == spec.version
    assert {"session_id", "stage"}.issubset(
        set(spec.parameters_schema().get("required", []))
    )


def test_continuity_tool_validates_enums_before_execution():
    spec = registry.get_spec("archive_chapter_after_write")
    assert spec is not None
    value = spec.validate_input({"chapter_id": "chapter-1", "mode": "manual"})
    assert value.mode == "manual"
    with pytest.raises(ValidationError):
        spec.validate_input({"chapter_id": "chapter-1", "mode": "silent"})


def test_unmigrated_tool_keeps_legacy_schema_projection():
    tool = registry.get("list_projects")
    spec = registry.get_spec("list_projects")
    assert tool is not None and spec is not None
    schema = spec.parameters_schema()
    assert schema["properties"] == tool.input_schema
    assert schema.get("required", []) == tool.required


def test_frontend_catalog_comes_from_tool_specs():
    metadata = {
        item["name"]: item for item in registry.list_for_frontend()
    }["inspect_story_granularity"]
    assert metadata["version"] == "3.0.0"
    assert metadata["writes_project_data"] is False
