"""Runtime checks for the exact tool schemas shown to models."""

from __future__ import annotations

import asyncio

import fastjsonschema
import pytest
from pydantic import ValidationError

from app.architecture.tool_spec import ToolInputSchemaValidationError
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry


def test_legacy_schema_rejects_arguments_before_handler_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False
    original_get_handler = registry.get_handler

    async def handler(_db: object, _project_id: str, _args: dict) -> dict:
        nonlocal called
        called = True
        return {"tool": "search_characters", "status": "ok", "data": []}

    monkeypatch.setattr(
        registry,
        "get_handler",
        lambda name: handler if name == "search_characters" else original_get_handler(name),
    )

    result = asyncio.run(
        execute_workspace_action(
            None,  # type: ignore[arg-type]
            "project-1",
            {"tool": "search_characters", "arguments": {}},
        )
    )

    assert called is False
    assert result["status"] == "error"
    assert result["data"] == {
        "reason": "native_tool_contract_invalid",
        "failure_class": "invalid_tool_arguments",
        "path": "$",
        "rule": "required",
        "retryable": True,
    }
    assert "query" in result["detail"]


def test_imported_file_typed_contract_matches_handler_ranges() -> None:
    list_schema = registry.get_spec("list_imported_files").parameters_schema()
    read_spec = registry.get_spec("read_imported_file")
    assert read_spec is not None
    read_schema = read_spec.parameters_schema()

    assert set(list_schema["properties"]) == {"cursor", "limit"}
    assert list_schema["properties"]["limit"]["maximum"] == 3
    assert set(read_schema["properties"]) == {"filename", "max_size", "offset_chars"}
    assert read_schema["properties"]["max_size"]["default"] == 4_000
    assert read_schema["properties"]["max_size"]["maximum"] == 4_000
    assert read_spec.validate_input(
        {"filename": "notes.txt", "max_size": 4_000, "offset_chars": 8_000}
    ).offset_chars == 8_000


def test_every_model_visible_tool_schema_compiles() -> None:
    for spec in registry.all_specs():
        fastjsonschema.compile(spec.parameters_schema(), use_default=False)


@pytest.mark.parametrize(
    ("tool_name", "identity"),
    [
        (
            "patch_creation_artifact",
            {"session_id": "session-1", "artifact": "macro_outline"},
        ),
        ("patch_creation_entity", {"entity_id": "entity-1"}),
    ],
)
@pytest.mark.parametrize(
    "changes",
    [
        [],
        [{"path": "/title"}],
        [{"action": "resize", "path": "/volumes"}],
        [{"action": "set", "op": "replace", "path": "/title", "value": "新标题"}],
    ],
)
def test_creation_patch_schema_rejects_calls_the_handler_cannot_apply(
    tool_name: str,
    identity: dict,
    changes: list[dict],
) -> None:
    spec = registry.get_spec(tool_name)
    assert spec is not None
    arguments = {
        **identity,
        "expected_revision": 3,
        "changes": changes,
    }
    validator = fastjsonschema.compile(spec.parameters_schema(), use_default=False)

    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        validator(arguments)
    with pytest.raises((ValidationError, ToolInputSchemaValidationError)):
        spec.validate_input(arguments)


@pytest.mark.parametrize(
    ("tool_name", "identity"),
    [
        (
            "patch_creation_artifact",
            {"session_id": "session-1", "artifact": "macro_outline"},
        ),
        ("patch_creation_entity", {"entity_id": "entity-1"}),
    ],
)
@pytest.mark.parametrize(
    "change",
    [
        {"action": "set", "path": "/title", "value": "新标题"},
        {"op": "add", "path": "/volumes/-", "value": {"title": "第二卷"}},
        {"action": "resize", "path": "/volumes", "target_count": 2},
    ],
)
def test_creation_patch_schema_accepts_each_supported_operation_form(
    tool_name: str,
    identity: dict,
    change: dict,
) -> None:
    spec = registry.get_spec(tool_name)
    assert spec is not None
    arguments = {
        **identity,
        "expected_revision": 3,
        "changes": [change],
    }

    fastjsonschema.compile(spec.parameters_schema(), use_default=False)(arguments)
    assert spec.validate_input(arguments).changes
