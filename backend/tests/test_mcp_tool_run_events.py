"""Tests for MCP tool auto-instrumentation with run events."""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.adapter import (
    _build_args_summary,
    _format_tool_result,
    _log_run_tool_event,
    execute_tool,
)
from app.services.workspace.registry import registry


class BuildArgsSummaryTest(unittest.TestCase):
    """Verify _build_args_summary logs structure, never string payloads."""

    def test_string_values_are_summarized_and_numbers_are_preserved(self):
        result = _build_args_summary({"query": "test", "limit": 10})
        self.assertIn("query: [str:4]", result)
        self.assertNotIn("query: test", result)
        self.assertIn("limit: 10", result)

    def test_sensitive_content_is_explicitly_redacted(self):
        result = _build_args_summary({"content": "x" * 500})
        self.assertIn("[redacted]", result)
        self.assertNotIn("xxxxx", result)

    def test_list_replaced(self):
        result = _build_args_summary({"items": [1, 2, 3]})
        self.assertIn("[list:3]", result)

    def test_dict_replaced(self):
        result = _build_args_summary({"config": {"key": "value"}})
        self.assertIn("[dict:1]", result)

    def test_total_truncated(self):
        args = {f"field_{i}": f"value_{i}" for i in range(50)}
        result = _build_args_summary(args)
        self.assertLessEqual(len(result), 300)

    def test_untrusted_argument_key_cannot_smuggle_secret_path_or_content(self):
        secret_key = "sk-secret /private/project manuscript content"
        result = _build_args_summary({secret_key: 7})
        self.assertNotIn(secret_key, result)
        self.assertNotIn("/private/project", result)
        self.assertEqual(result, "[field]: 7")


class FormatToolResultTest(unittest.TestCase):
    def test_ready_context_manifest_is_not_an_mcp_error(self):
        tool = registry.get("prepare_task_context")
        self.assertIsNotNone(tool)
        result = _format_tool_result(
            tool,
            {
                "tool": "prepare_task_context",
                "status": "ready",
                "detail": "Task context prepared.",
                "data": {"context_manifest_id": "manifest-1"},
            },
        )

        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0]["text"])
        self.assertEqual(payload["status"], "ready")


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
    def test_run_id_is_preserved_for_context_governance_tools(self, mock_log, mock_exec):
        mock_exec.return_value = {
            "tool": "prepare_task_context",
            "status": "ok",
            "detail": "ok",
            "data": {"context_manifest_id": "manifest-1"},
        }
        db = MagicMock()
        asyncio.run(execute_tool(
            db, "p1", "prepare_task_context",
            {"task_type": "writing", "run_id": "run123"},
            allowed_tiers={"readonly"},
        ))

        passed_args = mock_exec.call_args.args[2]["arguments"]
        self.assertEqual(passed_args["run_id"], "run123")

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
