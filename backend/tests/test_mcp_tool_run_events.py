"""Tests for MCP tool auto-instrumentation with run events."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import execute_tool, _build_args_summary, _log_run_tool_event


class BuildArgsSummaryTest(unittest.TestCase):
    """Verify _build_args_summary produces safe, truncated output."""

    def test_short_args_preserved(self):
        result = _build_args_summary({"query": "test", "limit": 10})
        self.assertIn("query: test", result)
        self.assertIn("limit: 10", result)

    def test_long_string_truncated(self):
        result = _build_args_summary({"content": "x" * 500})
        self.assertIn("[500 chars]", result)
        self.assertNotIn("xxxxx", result)

    def test_list_replaced(self):
        result = _build_args_summary({"items": [1, 2, 3]})
        self.assertIn("[list]", result)

    def test_dict_replaced(self):
        result = _build_args_summary({"config": {"key": "value"}})
        self.assertIn("[dict]", result)

    def test_total_truncated(self):
        args = {f"field_{i}": f"value_{i}" for i in range(50)}
        result = _build_args_summary(args)
        self.assertLessEqual(len(result), 300)


class LogRunToolEventTest(unittest.TestCase):
    """Verify _log_run_tool_event calls add_event correctly."""

    @patch("app.services.external_agent.run_service.add_event")
    def test_calls_add_event(self, mock_add_event):
        db = MagicMock()
        _log_run_tool_event(
            db, "run1", "tool_start", "list_projects", {},
            status="running",
        )
        mock_add_event.assert_called_once()
        call_args = mock_add_event.call_args
        self.assertEqual(call_args[0][1], "run1")  # run_id
        self.assertEqual(call_args[0][2], "tool_start")  # event_type

    @patch("app.services.external_agent.run_service.add_event")
    def test_tool_result_event(self, mock_add_event):
        db = MagicMock()
        _log_run_tool_event(
            db, "run1", "tool_result", "search_chapters", {"query": "test"},
            status="ok",
            detail="Found 3 chapters",
        )
        mock_add_event.assert_called_once()
        call_args = mock_add_event.call_args
        self.assertEqual(call_args[0][2], "tool_result")

    @patch("app.services.external_agent.run_service.add_event", side_effect=Exception("DB error"))
    def test_failure_does_not_raise(self, mock_add_event):
        db = MagicMock()
        # Should not raise
        _log_run_tool_event(db, "run1", "tool_start", "test", {})


class ExecuteToolRunIdTest(unittest.TestCase):
    """Verify execute_tool handles run_id correctly."""

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    @patch("app.mcp.adapter._log_run_tool_event")
    def test_run_id_stripped_from_arguments(self, mock_log, mock_exec):
        mock_exec.return_value = {"tool": "list_projects", "status": "ok", "detail": "ok", "data": {}}
        db = MagicMock()
        args = {"query": "test", "run_id": "run123"}
        asyncio.run(execute_tool(
            db, "p1", "list_projects", args,
            allowed_tiers={"readonly"},
        ))
        # run_id should be stripped before calling executor
        call_args = mock_exec.call_args
        passed_args = call_args[0][2]["arguments"]
        self.assertNotIn("run_id", passed_args)

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    @patch("app.mcp.adapter._log_run_tool_event")
    def test_run_id_triggers_telemetry(self, mock_log, mock_exec):
        mock_exec.return_value = {"tool": "list_projects", "status": "ok", "detail": "ok", "data": {}}
        db = MagicMock()
        args = {"run_id": "run123"}
        asyncio.run(execute_tool(
            db, "p1", "list_projects", args,
            allowed_tiers={"readonly"},
        ))
        # Should have been called twice: tool_start and tool_result
        self.assertEqual(mock_log.call_count, 2)
        calls = mock_log.call_args_list
        self.assertEqual(calls[0][0][2], "tool_start")  # event_type
        self.assertEqual(calls[1][0][2], "tool_result")

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    @patch("app.mcp.adapter._log_run_tool_event")
    def test_no_run_id_no_telemetry(self, mock_log, mock_exec):
        mock_exec.return_value = {"tool": "list_projects", "status": "ok", "detail": "ok", "data": {}}
        db = MagicMock()
        asyncio.run(execute_tool(
            db, "p1", "list_projects", {},
            allowed_tiers={"readonly"},
        ))
        mock_log.assert_not_called()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    @patch("app.mcp.adapter._log_run_tool_event")
    def test_run_id_passed_explicitly(self, mock_log, mock_exec):
        mock_exec.return_value = {"tool": "list_projects", "status": "ok", "detail": "ok", "data": {}}
        db = MagicMock()
        asyncio.run(execute_tool(
            db, "p1", "list_projects", {},
            allowed_tiers={"readonly"},
            run_id="explicit_run",
        ))
        self.assertEqual(mock_log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
