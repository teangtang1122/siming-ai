"""Typed workspace tool catalog tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.architecture.tool_spec import LegacyToolInput
from app.modules.creation.interfaces.tool_definitions import (
    TOOL_DEFINITIONS as CREATION_TOOL_DEFINITIONS,
)
from app.services.workspace.registry import registry


def _openai_parameters(name: str) -> dict:
    schema = next(item for item in registry.get_schemas() if item["function"]["name"] == name)
    return schema["function"]["parameters"]


def test_creation_tool_schema_has_one_typed_source():
    spec = registry.get_spec("generate_creation_artifact")
    assert spec is not None
    assert spec.version == "3.0.0"
    assert _openai_parameters(spec.name) == spec.parameters_schema()
    assert spec.mcp_schema()["inputSchema"] == spec.parameters_schema()
    assert {"session_id", "artifact", "expected_revision"}.issubset(
        set(spec.parameters_schema().get("required", []))
    )


@pytest.mark.parametrize(
    ("tool_name", "extra"),
    [
        ("generate_creation_artifact", {}),
        ("refine_creation_artifact", {"instruction": "调整核心冲突"}),
        ("regenerate_creation_artifact", {}),
    ],
)
def test_artifact_generation_uses_default_model_without_a_model_gate(tool_name, extra):
    spec = registry.get_spec(tool_name)
    assert spec is not None
    validated = spec.validate_input({
        "session_id": "session-1",
        "artifact": "concepts",
        "expected_revision": 3,
        **extra,
    })
    assert validated.model == ""

    validated_without_model = spec.validate_input({
        "session_id": "session-1",
        "artifact": "concepts",
        "expected_revision": 3,
        "use_model": False,
        **extra,
    })
    assert validated_without_model.use_model is False


def test_removed_creation_tools_are_absent_from_every_catalog():
    removed = {
        "get_novel_creation_session",
        "generate_novel_creation_stage",
        "submit_novel_creation_stage",
    }
    assert all(registry.get(name) is None for name in removed)
    assert removed.isdisjoint({item["function"]["name"] for item in registry.get_schemas()})


def test_every_creation_session_tool_has_a_typed_input_contract():
    unrelated_generators = {
        "design_plot", "chapter_writer", "character_writer", "outline_writer",
        "worldbuilding_writer", "rewrite_text", "expand_text", "continue_text",
        "roleplay_character", "dialogue_battle",
    }
    for definition in CREATION_TOOL_DEFINITIONS:
        if definition.name in unrelated_generators:
            continue
        spec = registry.get_spec(definition.name)
        assert spec is not None
        assert spec.input_model is not LegacyToolInput, definition.name
        assert spec.version == "3.0.0"


def test_creation_import_contract_rejects_unknown_strategy_and_artifact():
    spec = registry.get_spec("apply_creation_import")
    assert spec is not None
    with pytest.raises(ValidationError):
        spec.validate_input({
            "import_id": "import-1",
            "selected_artifacts": ["unknown"],
            "strategy": "replace_everything",
            "expected_revision": 3,
        })


def test_creation_operation_contract_requires_operation_id():
    spec = registry.get_spec("cancel_creation_operation")
    assert spec is not None
    with pytest.raises(ValidationError):
        spec.validate_input({})


def test_unmigrated_tool_keeps_legacy_schema_projection():
    tool = registry.get("list_projects")
    spec = registry.get_spec("list_projects")
    assert tool is not None and spec is not None
    schema = spec.parameters_schema()
    assert schema["properties"] == tool.input_schema
    assert schema.get("required", []) == tool.required


@pytest.mark.parametrize("field", ["facts", "candidates"])
@pytest.mark.parametrize("invalid", ['[{"payload": {}}]', {"payload": {}}, ["record"], None])
def test_cataloging_rejects_encoded_arrays_before_execution(field, invalid):
    spec = registry.get_spec(f"save_external_cataloging_{field}")
    with pytest.raises(ValidationError):
        spec.validate_input({"job_id": "job-1", "chapter_id": "chapter-1", field: invalid})


def test_cataloging_fact_contract_preserves_native_structures_and_rejects_unknown_type():
    spec = registry.get_spec("save_external_cataloging_facts")
    record = {"fact_type": "chapter_overview", "payload": {"summary": "雨夜发现缺失的七分钟"}}
    validated = spec.validate_input({"job_id": "job-1", "chapter_id": "chapter-1", "facts": [record]})
    assert validated.facts[0].payload == record["payload"]
    assert _openai_parameters(spec.name) == spec.mcp_schema()["inputSchema"]
    with pytest.raises(ValidationError):
        spec.validate_input({"job_id": "job-1", "chapter_id": "chapter-1",
                             "facts": [{**record, "fact_type": "人物事实"}]})
