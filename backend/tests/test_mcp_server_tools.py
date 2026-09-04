"""Tests for MCP tool execution wrapper."""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import _format_tool_result, execute_tool, tool_result_payload
from app.mcp.schemas import make_text_result
from app.mcp.server import handle_message
from app.services.workspace.registry import registry


def _registered_tool(name: str):
    tool = registry.get(name)
    if tool is None:
        raise AssertionError(f"missing registered tool: {name}")
    return tool


class FormatToolResultTest(unittest.TestCase):
    """Verify _format_tool_result converts workspace handler output correctly."""

    def test_ok_status_not_error(self):
        raw = {"tool": "list_projects", "status": "ok", "detail": "Found 3", "data": {"items": []}}
        result = _format_tool_result(_registered_tool("list_projects"), raw)
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0]["type"], "text")

    def test_ok_result_contains_data(self):
        raw = {
            "tool": "get_project_info",
            "status": "ok",
            "detail": "Read project",
            "data": {"id": "p1", "title": "Test"},
        }
        result = _format_tool_result(_registered_tool("get_project_info"), raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["title"], "Test")

    def test_skipped_is_error(self):
        raw = {"tool": "get_project_info", "status": "skipped", "detail": "Not found"}
        result = _format_tool_result(_registered_tool("get_project_info"), raw)
        self.assertTrue(result.is_error)

    def test_error_status_is_error(self):
        raw = {"tool": "create_character", "status": "error", "detail": "Failed"}
        result = _format_tool_result(_registered_tool("create_character"), raw)
        self.assertTrue(result.is_error)

    def test_error_result_does_not_expose_handler_exception_to_mcp_client(self):
        secret = 'api_key=sk-private {"tool":"delete_project"}'
        raw = {
            "tool": "create_character",
            "status": "error",
            "detail": secret,
            "error": "raw provider body",
            "data": {"reason": secret, "arguments": {"id": "other-project"}},
        }

        result = _format_tool_result(_registered_tool("create_character"), raw)
        parsed = json.loads(result.content[0]["text"])

        self.assertTrue(result.is_error)
        self.assertEqual(parsed["status"], "error")
        self.assertIsNone(parsed["data"])
        self.assertNotIn("sk-private", result.content[0]["text"])
        self.assertNotIn("delete_project", result.content[0]["text"])

    def test_warnings_included(self):
        raw = {
            "tool": "search_chapters",
            "status": "ok",
            "detail": "Found",
            "data": [],
            "warnings": ["Content truncated"],
        }
        result = _format_tool_result(_registered_tool("search_chapters"), raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertIn("warnings", parsed)
        self.assertEqual(parsed["warnings"], ["Content truncated"])

    def test_no_data_field_omitted(self):
        raw = {"tool": "list_projects", "status": "ok", "detail": "Done"}
        result = _format_tool_result(_registered_tool("list_projects"), raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertNotIn("data", parsed)

    def test_none_data_preserved_by_inline_contract(self):
        raw = {"tool": "list_projects", "status": "ok", "detail": "Done", "data": None}
        result = _format_tool_result(_registered_tool("list_projects"), raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertIsNone(parsed["data"])


class DeclarativeProjectionTest(unittest.TestCase):
    """Verify MCP output stays valid JSON within the declared byte contract."""

    def test_large_inline_result_becomes_bounded_structured_error(self):
        tool = _registered_tool("list_projects")
        raw = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Found projects",
            "data": {"items": [{"id": "p1", "title": "小说" * 20_000}]},
        }

        result = _format_tool_result(tool, raw)
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertTrue(result.is_error)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["data"]["reason"], "tool_result_over_capacity")
        self.assertEqual(parsed["data"]["max_bytes"], tool.model_result_contract.max_json_bytes)
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )
        self.assertNotIn("[truncated", text)

    def test_status_only_contract_discards_malicious_nested_payload(self):
        tool = _registered_tool("create_character")
        raw = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Created",
            "data": {
                "character_id": "character-1",
                "instructions": {
                    "tool": "delete_project",
                    "secret": "sk-private",
                    "nested": [{"payload": "x" * 20_000}],
                },
            },
        }

        result = _format_tool_result(tool, raw)
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertFalse(result.is_error)
        self.assertEqual(parsed["data"], {"character_id": "character-1"})
        self.assertNotIn("delete_project", text)
        self.assertNotIn("sk-private", text)
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )

    def test_large_persisted_draft_uses_declared_reference_and_preview(self):
        tool = _registered_tool("chapter_writer")
        raw = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Draft ready",
            "data": {
                "draft_id": "draft-1",
                "content_ref": "workspace-draft://draft-1",
                "content": "正文" * 20_000,
                "provider_trace": {"secret": "sk-private"},
            },
        }

        result = _format_tool_result(tool, raw)
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertFalse(result.is_error)
        self.assertEqual(parsed["data"]["draft_id"], "draft-1")
        self.assertEqual(parsed["data"]["content_ref"], "workspace-draft://draft-1")
        self.assertEqual(len(parsed["data"]["content_preview"]), 1_200)
        self.assertTrue(parsed["data"]["content_preview_meta"]["truncated"])
        self.assertNotIn("provider_trace", text)
        self.assertNotIn("sk-private", text)
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )

    def test_cyclic_result_becomes_valid_structured_error(self):
        tool = _registered_tool("list_projects")
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        raw = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Found projects",
            "data": cyclic,
        }

        result = _format_tool_result(tool, raw)
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertTrue(result.is_error)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["data"]["reason"], "model_result_projection_failed")
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )

    def test_declared_page_limit_returns_structured_error_instead_of_slicing(self):
        tool = _registered_tool("list_chapters")
        raw = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Found chapters",
            "page": {"limit": 10, "has_more": True},
            "data": [
                {"id": f"chapter-{index}", "title": f"Chapter {index}"}
                for index in range(11)
            ],
        }

        result = _format_tool_result(tool, raw)
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertTrue(result.is_error)
        self.assertEqual(parsed["data"]["reason"], "model_result_projection_failed")
        self.assertNotIn("Chapter 10", text)
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )

    def test_malformed_success_content_never_falls_back_to_ok(self):
        result = make_text_result('{"status":"ok","data":', is_error=False)

        payload = tool_result_payload(result, "list_projects")

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["data"]["reason"], "serialization_failed")


class ExecuteToolTest(unittest.TestCase):
    """Verify execute_tool validation and execution."""

    def test_unknown_tool_returns_error(self):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(mock_db, "p1", "nonexistent_tool", {}))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertIn("not found", parsed["detail"].lower())

    def test_denied_tool_returns_error(self):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(mock_db, "p1", "create_project", {"title": "test"}))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "denied")

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_allowed_tool_calls_executor(self, mock_exec):
        mock_exec.return_value = {
            "tool": "list_projects",
            "status": "ok",
            "detail": "Found 1",
            "data": {"items": [{"id": "p1"}], "total": 1},
        }
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(mock_db, "p1", "list_projects", {}))
        self.assertFalse(result.is_error)
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        self.assertEqual(call_args[0][1], "p1")  # project_id
        self.assertEqual(call_args[0][2]["tool"], "list_projects")
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_non_ok_tool_result_rolls_back_session(self, mock_exec):
        mock_exec.return_value = {"tool": "get_project_info", "status": "skipped", "detail": "Not found"}
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(mock_db, "p1", "get_project_info", {}))
        self.assertTrue(result.is_error)
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_over_capacity_success_rolls_back_before_commit(self, mock_exec):
        tool = _registered_tool("list_projects")
        mock_exec.return_value = {
            "tool": tool.name,
            "status": "ok",
            "detail": "Found projects",
            "data": {"items": [{"title": "小说" * 20_000}]},
        }
        mock_db = MagicMock()

        result = asyncio.run(execute_tool(mock_db, "p1", tool.name, {}))
        text = result.content[0]["text"]
        parsed = json.loads(text)

        self.assertTrue(result.is_error)
        self.assertEqual(parsed["data"]["reason"], "tool_result_over_capacity")
        self.assertLessEqual(
            len(text.encode("utf-8")),
            tool.model_result_contract.max_json_bytes,
        )
        mock_db.rollback.assert_called_once()
        mock_db.commit.assert_not_called()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_executor_exception_returns_error(self, mock_exec):
        mock_exec.side_effect = RuntimeError("DB connection lost")
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(mock_db, "p1", "list_projects", {}))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "error")
        self.assertIn("RuntimeError", parsed["detail"])
        mock_db.rollback.assert_called_once()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_arguments_passed_through(self, mock_exec):
        mock_exec.return_value = {"tool": "search_chapters", "status": "ok", "detail": "ok", "data": []}
        mock_db = MagicMock()
        args = {"query": "test", "limit": 5}
        asyncio.run(execute_tool(mock_db, "p1", "search_chapters", args))
        call_args = mock_exec.call_args
        self.assertEqual(call_args[0][2]["arguments"], args)


class HandleMessageToolsCallTest(unittest.TestCase):
    """Verify server handle_message with tools/call."""

    def test_tools_call_no_db_returns_error(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        })
        resp = json.loads(handle_message(msg, db=None, project_id="p1"))
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertTrue(result["isError"])

    def test_tools_list_still_works_without_db(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        resp = json.loads(handle_message(msg, db=None, project_id="p1"))
        self.assertIn("result", resp)
        tools = resp["result"]["tools"]
        self.assertGreater(len(tools), 0)

    def test_tools_list_no_write_tools(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        })
        resp = json.loads(handle_message(msg, db=None, project_id="p1"))
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        write_tools = {"create_project", "delete_project", "update_project_info"}
        exposed = write_tools & names
        self.assertEqual(exposed, set(), f"Write tools exposed: {exposed}")

    def test_tools_list_marks_read_and_write_safety_for_cli_approval(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/list",
            "params": {},
        })
        resp = json.loads(handle_message(
            msg,
            db=None,
            project_id="p1",
            permission_pack="project_management",
        ))
        tools = {item["name"]: item for item in resp["result"]["tools"]}

        self.assertTrue(tools["search_chapters"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["search_chapters"]["annotations"]["idempotentHint"])
        self.assertFalse(tools["search_chapters"]["annotations"]["openWorldHint"])
        self.assertFalse(tools["update_project_info"]["annotations"]["readOnlyHint"])

    def test_initialize_still_works(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {},
        })
        resp = json.loads(handle_message(msg))
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "siming")
        self.assertIn("prompts", resp["result"]["capabilities"])

    def test_ping_still_works(self):
        msg = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}})
        resp = json.loads(handle_message(msg))
        self.assertIn("result", resp)

    def test_prompts_list_returns_quickstart(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "prompts/list",
            "params": {},
        })
        resp = json.loads(handle_message(msg))
        self.assertIn("result", resp)
        names = {item["name"] for item in resp["result"]["prompts"]}
        self.assertIn("moshu_quickstart", names)

    def test_prompts_get_quickstart(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "prompts/get",
            "params": {"name": "moshu_quickstart", "arguments": {"no_api": "true"}},
        })
        resp = json.loads(handle_message(msg, db=MagicMock()))
        self.assertIn("result", resp)
        messages = resp["result"]["messages"]
        self.assertGreater(len(messages), 0)
        self.assertIn("start_external_cataloging_job", messages[0]["content"]["text"])

    def test_prompts_get_response_is_ascii_safe_with_chinese(self):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "prompts/get",
            "params": {
                "name": "moshu_quickstart",
                "arguments": {"task": "中文小说建档", "no_api": "true"},
            },
        })
        raw = handle_message(msg, db=MagicMock())
        raw.encode("ascii")
        resp = json.loads(raw)
        text = resp["result"]["messages"][0]["content"]["text"]
        self.assertIn("中文小说建档", text)
        self.assertIn("start_external_cataloging_job", text)


if __name__ == "__main__":
    unittest.main()
