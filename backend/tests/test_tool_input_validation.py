"""Runtime checks for the exact tool schemas shown to models."""

from __future__ import annotations

import asyncio

import fastjsonschema
import pytest
from pydantic import ValidationError

from app.architecture.tool_spec import ToolInputSchemaValidationError
from app.modules.creation.domain.tool_specs import PatchCreationArtifactInput
from app.routers.novel_creation_aux_routes import (
    NovelCreationArtifactPatchRequest,
    NovelCreationEntityPatchRequest,
    NovelCreationSessionPatchRequest,
)
from app.services.novel_creation_agent import _domain_tool_schemas
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


@pytest.mark.parametrize("name", ["patch_creation_artifact", "patch_creation_entity"])
def test_creation_patch_operation_path_is_inline_and_required_for_native_models(name):
    schema = registry.get_spec(name).parameters_schema()
    item = schema["properties"]["changes"]["items"]
    assert "$ref" not in item
    assert "path" in item["required"]
    assert item["properties"]["path"]["type"] == "string"


@pytest.mark.parametrize("name", ["patch_creation_artifact", "patch_creation_entity"])
def test_native_creation_patch_schema_is_the_registered_api_and_mcp_contract(name):
    schema = next(
        tool["function"]["parameters"] for tool in _domain_tool_schemas()
        if tool["function"]["name"] == name
    )
    assert schema == registry.get_spec(name).parameters_schema()
    item = schema["properties"]["changes"]["items"]
    assert item["required"] == ["path"]
    assert item["properties"]["action"]["type"] == "string"
    assert item["properties"]["op"]["type"] == "string"
    assert item["oneOf"] == [{"required": ["action"]}, {"required": ["op"]}]

    arguments = {
        "expected_revision": 1,
        "changes": [{"path": "/genre", "action": "set", "value": "玄幻"}],
    }
    arguments.update(
        {"session_id": "session-1", "artifact": "constraints"}
        if name == "patch_creation_artifact" else {"entity_id": "entity-1"}
    )
    validate = fastjsonschema.compile(schema, use_default=False)
    validate(arguments)
    arguments["changes"] = [{"path": "/genre", "op": "replace", "value": "玄幻"}]
    validate(arguments)
    arguments["changes"] = [{"path": "/genre", "action": "set", "op": "replace", "value": "玄幻"}]
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        validate(arguments)
    arguments["changes"] = [{"path": "/genre", "action": "set", "value": "玄幻"}]
    del arguments["changes"][0]["path"]
    with pytest.raises(fastjsonschema.JsonSchemaValueException):
        validate(arguments)


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
        [{"action": "set", "path": "/title"}],
        [{"op": "add", "path": "/tags/-"}],
        [{"action": "set", "path": "title", "value": "新标题"}],
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


def test_typed_patch_input_validates_against_the_same_exported_schema() -> None:
    spec = registry.get_spec("patch_creation_artifact")
    typed = PatchCreationArtifactInput(
        session_id="session-1",
        artifact="constraints",
        expected_revision=3,
        changes=[{"path": "/genre", "action": "set", "value": "玄幻"}],
    )
    assert spec.validate_input(typed) == typed


def test_session_patch_contract_requires_nested_form_and_matches_rest() -> None:
    spec = registry.get_spec("patch_creation_session")
    assert spec is not None
    schema = spec.parameters_schema()
    validator = fastjsonschema.compile(schema, use_default=False)
    assert schema["properties"]["changes"]["properties"]["form"]["properties"]["genre"]["type"] == "string"
    arguments = {
        "session_id": "session-1", "expected_revision": 2,
        "changes": {"form": {"genre": "玄幻", "brief": "废柴逆袭"}},
    }
    validator(arguments)
    assert spec.validate_input(arguments).changes.form.brief == "废柴逆袭"
    rest = NovelCreationSessionPatchRequest.model_validate({
        "expected_revision": 2, "form": {"genre": "玄幻", "brief": "废柴逆袭"},
    })
    assert rest.model_dump(exclude_unset=True)["form"] == arguments["changes"]["form"]
    for changes in (
        {"genre": "玄幻"}, {"user_brief": "废柴逆袭"},
        {"form": {}}, {"form": {"unexpected": "ignored before"}},
    ):
        invalid = {**arguments, "changes": changes}
        with pytest.raises(fastjsonschema.JsonSchemaValueException):
            validator(invalid)
        with pytest.raises((ValidationError, ToolInputSchemaValidationError)):
            spec.validate_input(invalid)


@pytest.mark.parametrize(
    ("tool_name", "request_model"),
    [
        ("patch_creation_artifact", NovelCreationArtifactPatchRequest),
        ("patch_creation_entity", NovelCreationEntityPatchRequest),
    ],
)
def test_rest_patch_request_uses_the_same_operation_schema(tool_name, request_model) -> None:
    tool_change = registry.get_spec(tool_name).parameters_schema()["properties"]["changes"]["items"]
    rest_schema = request_model.model_json_schema()
    assert rest_schema["$defs"]["CreationPatchOperation"] == tool_change

    for change in (
        {"path": "/genre", "action": "set", "value": "玄幻"},
        {"path": "/tags/-", "op": "add", "value": "悬疑"},
    ):
        request = request_model.model_validate({"expected_revision": 1, "changes": [change]})
        assert request.changes[0].model_dump(exclude_unset=True) == change

    for invalid in (
        {"action": "set", "value": "缺路径"},
        {"path": "/genre", "value": "缺动作"},
        {"path": "/genre", "action": "set", "op": "replace", "value": "重复动作"},
        {"path": "/genre", "action": None, "op": "replace", "value": "空动作"},
        {"path": "/genre", "action": "set"},
    ):
        with pytest.raises(ValidationError):
            request_model.model_validate({"expected_revision": 1, "changes": [invalid]})
