"""Tests for MCP tool execution wrapper."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import execute_tool, _format_tool_result, _truncate_content
from app.mcp.schemas import McpToolResult, make_text_result
from app.mcp.server import handle_message


class FormatToolResultTest(unittest.TestCase):
    """Verify _format_tool_result converts workspace handler output correctly."""

    def test_ok_status_not_error(self):
        raw = {"tool": "list_projects", "status": "ok", "detail": "Found 3", "data": {"items": []}}
        result = _format_tool_result(raw)
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
        result = _format_tool_result(raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["title"], "Test")

    def test_skipped_is_error(self):
        raw = {"tool": "get_project_info", "status": "skipped", "detail": "Not found"}
        result = _format_tool_result(raw)
        self.assertTrue(result.is_error)

    def test_error_status_is_error(self):
        raw = {"tool": "create_chapter", "status": "error", "detail": "Failed"}
        result = _format_tool_result(raw)
        self.assertTrue(result.is_error)

    def test_warnings_included(self):
        raw = {
            "tool": "search_chapters",
            "status": "ok",
            "detail": "Found",
            "data": [],
            "warnings": ["Content truncated"],
        }
        result = _format_tool_result(raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertIn("warnings", parsed)
        self.assertEqual(parsed["warnings"], ["Content truncated"])

    def test_no_data_field_omitted(self):
        raw = {"tool": "test", "status": "ok", "detail": "Done"}
        result = _format_tool_result(raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertNotIn("data", parsed)

    def test_none_data_omitted(self):
        raw = {"tool": "test", "status": "ok", "detail": "Done", "data": None}
        result = _format_tool_result(raw)
        parsed = json.loads(result.content[0]["text"])
        self.assertNotIn("data", parsed)


class TruncateContentTest(unittest.TestCase):
    """Verify content truncation for large outputs."""

    def test_short_text_not_truncated(self):
        text = "hello world"
        result = _truncate_content(text, limit=100)
        self.assertEqual(result, text)

    def test_long_text_truncated(self):
        text = "x" * 20000
        result = _truncate_content(text, limit=12000)
        self.assertIn("[truncated", result)
        self.assertIn("20000 chars", result)
        self.assertLess(len(result), 20000)

    def test_exact_limit_not_truncated(self):
        text = "a" * 12000
        result = _truncate_content(text, limit=12000)
        self.assertEqual(result, text)


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
        write_tools = {"create_project", "delete_project", "update_project_info", "create_chapter"}
        exposed = write_tools & names
        self.assertEqual(exposed, set(), f"Write tools exposed: {exposed}")

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
