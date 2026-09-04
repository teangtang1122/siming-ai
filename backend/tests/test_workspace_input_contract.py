"""Malformed model parameters fail before business writes on every transport."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import model_tool_result_projector


def test_stringified_patch_is_rejected_with_safe_actionable_diagnostic():
    handler = AsyncMock()
    args = {
        "session_id": "session-1", "artifact": "concepts", "expected_revision": 2,
        "changes": json.dumps([{"path": "/", "action": "set", "value": {
            "private_input": "sk-do-not-reflect-input-values",
        }}]),
    }
    with patch.object(registry, "get_handler", return_value=handler):
        result = asyncio.run(execute_workspace_action(Mock(), "", {
            "tool": "patch_creation_artifact", "arguments": args,
        }))
    handler.assert_not_awaited()
    projected = model_tool_result_projector.project(registry.get("patch_creation_artifact"), result)
    assert projected.payload["data"]["reason"] == "native_tool_contract_invalid"
    assert "不能编码成 JSON 字符串" in projected.payload["detail"]
    assert "sk-do-not-reflect-input-values" not in projected.content
    assert isinstance(args["changes"], str)


def test_native_patch_array_reaches_the_single_business_handler_unchanged():
    handler = AsyncMock(return_value={"tool": "patch_creation_artifact", "status": "ok", "data": None})
    args = {
        "session_id": "session-1", "artifact": "concepts", "expected_revision": 2,
        "changes": [{"path": "/", "action": "set", "value": {
            "options": [{"id": "concept-1", "title": "潮痕档案"}],
            "selected_concept_id": "concept-1",
        }}],
        "_context_execution_route": "external_mcp",
    }
    db = Mock()
    with patch.object(registry, "get_handler", return_value=handler):
        result = asyncio.run(execute_workspace_action(db, "", {
            "tool": "patch_creation_artifact", "arguments": args,
        }))
    handler.assert_awaited_once_with(db, "", args)
    assert result["status"] == "ok"


@pytest.mark.parametrize("nodes", ['[{"title":"Test","summary":"Future"}]', ["not an object"], []])
def test_outline_nodes_require_a_native_nonempty_object_array(nodes):
    handler = AsyncMock()
    with patch.object(registry, "get_handler", return_value=handler):
        result = asyncio.run(execute_workspace_action(Mock(), "p1", {
            "tool": "save_external_outline_draft", "arguments": {
                "context_manifest_id": "manifest", "context_selection_token": "selection", "nodes": nodes,
            },
        }))
    handler.assert_not_awaited()
    assert result["data"]["reason"] == "native_tool_contract_invalid"
    assert result["data"]["failure_class"] == "invalid_tool_arguments"
    assert result["data"]["path"].startswith("$")
    assert result["data"]["rule"]
