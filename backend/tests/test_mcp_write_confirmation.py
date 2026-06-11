"""Tests for MCP write confirmation token flow."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.permissions import (
    issue_confirmation_token,
    validate_confirmation_token,
    revoke_token,
    clear_expired_tokens,
    get_tier,
    is_allowed,
)
from app.mcp.adapter import execute_tool
from app.services.workspace.registry import registry


class IssueTokenTest(unittest.TestCase):
    """Verify token issuance."""

    def test_issue_returns_string(self):
        token = issue_confirmation_token("create_project")
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 10)

    def test_issue_unique_tokens(self):
        t1 = issue_confirmation_token("create_project")
        t2 = issue_confirmation_token("create_project")
        self.assertNotEqual(t1, t2)


class ValidateTokenTest(unittest.TestCase):
    """Verify token validation logic."""

    def setUp(self):
        # Clear any leftover tokens
        clear_expired_tokens()

    def test_valid_token_passes(self):
        token = issue_confirmation_token("create_project")
        is_valid, reason = validate_confirmation_token(token, "create_project")
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_missing_token_denied(self):
        is_valid, reason = validate_confirmation_token("", "create_project")
        self.assertFalse(is_valid)
        self.assertEqual(reason, "confirmation_required")

    def test_invalid_token_denied(self):
        is_valid, reason = validate_confirmation_token("fake-token", "create_project")
        self.assertFalse(is_valid)
        self.assertEqual(reason, "invalid_token")

    def test_wrong_tool_denied(self):
        token = issue_confirmation_token("create_project")
        is_valid, reason = validate_confirmation_token(token, "delete_project")
        self.assertFalse(is_valid)
        self.assertEqual(reason, "token_tool_mismatch")

    def test_single_use(self):
        token = issue_confirmation_token("create_project")
        # First use succeeds
        is_valid, _ = validate_confirmation_token(token, "create_project")
        self.assertTrue(is_valid)
        # Second use fails
        is_valid, reason = validate_confirmation_token(token, "create_project")
        self.assertFalse(is_valid)
        self.assertEqual(reason, "token_already_used")

    def test_expired_token_denied(self):
        token = issue_confirmation_token("create_project")
        # Manually expire it
        from app.mcp.permissions import _tokens
        _tokens[token].created_at -= 400  # older than TTL
        is_valid, reason = validate_confirmation_token(token, "create_project")
        self.assertFalse(is_valid)
        self.assertEqual(reason, "token_expired")


class RevokeTokenTest(unittest.TestCase):
    """Verify token revocation."""

    def test_revoke_existing(self):
        token = issue_confirmation_token("create_project")
        self.assertTrue(revoke_token(token))
        is_valid, reason = validate_confirmation_token(token, "create_project")
        self.assertFalse(is_valid)

    def test_revoke_nonexistent(self):
        self.assertFalse(revoke_token("nonexistent"))


class ClearExpiredTest(unittest.TestCase):
    """Verify expired token cleanup."""

    def test_clears_expired(self):
        from app.mcp.permissions import _tokens
        token = issue_confirmation_token("create_project")
        _tokens[token].created_at -= 400
        count = clear_expired_tokens()
        self.assertEqual(count, 1)
        self.assertNotIn(token, _tokens)


class WriteToolPermissionTest(unittest.TestCase):
    """Verify write tools require confirmation tokens."""

    def test_create_project_is_write_confirmed(self):
        td = registry.get("create_project")
        self.assertIsNotNone(td)
        self.assertEqual(get_tier(td), "write_confirmed")

    def test_write_tool_denied_in_readonly(self):
        td = registry.get("create_project")
        self.assertFalse(is_allowed(td, allowed_tiers={"readonly"}))

    def test_write_tool_denied_in_draft(self):
        td = registry.get("create_project")
        self.assertFalse(is_allowed(td, allowed_tiers={"readonly", "draft"}))

    def test_write_tool_allowed_in_write_confirmed(self):
        td = registry.get("create_project")
        self.assertTrue(is_allowed(td, allowed_tiers={"readonly", "draft", "write_confirmed"}))


class ExecuteToolConfirmationTest(unittest.TestCase):
    """Verify execute_tool checks confirmation tokens for write tools."""

    def test_write_without_token_denied(self):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "p1", "create_project",
            {"title": "test"},
            allowed_tiers={"readonly", "draft", "write_confirmed"},
        ))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "denied")
        self.assertIn("confirmation", parsed["detail"].lower())

    def test_write_with_invalid_token_denied(self):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "p1", "create_project",
            {"title": "test", "confirmation_token": "fake"},
            allowed_tiers={"readonly", "draft", "write_confirmed"},
        ))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["reason"], "invalid_token")

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_write_with_valid_token_executes(self, mock_exec):
        mock_exec.return_value = {
            "tool": "create_project",
            "status": "ok",
            "detail": "Created",
            "data": {"id": "p1"},
        }
        token = issue_confirmation_token("create_project")
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "p1", "create_project",
            {"title": "test", "confirmation_token": token},
            allowed_tiers={"readonly", "draft", "write_confirmed"},
        ))
        self.assertFalse(result.is_error)
        mock_exec.assert_called_once()

    def test_read_tool_no_token_needed(self):
        """Read tools should work without any confirmation token."""
        with patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = {
                "tool": "list_projects",
                "status": "ok",
                "detail": "Found",
                "data": {"items": [], "total": 0},
            }
            mock_db = MagicMock()
            result = asyncio.run(execute_tool(
                mock_db, "p1", "list_projects", {},
                allowed_tiers={"readonly"},
            ))
            self.assertFalse(result.is_error)

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_project_management_pack_allows_create_project_without_token(self, mock_exec):
        mock_exec.return_value = {
            "tool": "create_project",
            "status": "ok",
            "detail": "Created",
            "data": {"id": "p2"},
        }
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "", "create_project",
            {"title": "test"},
            permission_pack="project_management",
        ))
        self.assertFalse(result.is_error)
        mock_exec.assert_called_once()

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_mcp_project_id_argument_overrides_default_project(self, mock_exec):
        mock_exec.return_value = {
            "tool": "list_chapters",
            "status": "ok",
            "detail": "Found",
            "data": {"items": []},
        }
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "default-project", "list_chapters",
            {"project_id": "target-project"},
            permission_pack="readonly_collaboration",
        ))
        self.assertFalse(result.is_error)
        self.assertEqual(mock_exec.call_args.args[1], "target-project")

    @patch("app.services.workspace.executor.execute_workspace_action", new_callable=AsyncMock)
    def test_project_scoped_tool_without_project_id_is_denied(self, mock_exec):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "", "list_chapters",
            {},
            permission_pack="readonly_collaboration",
        ))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "denied")
        self.assertIn("project_id", parsed["detail"])
        mock_exec.assert_not_called()

    def test_trusted_pack_destructive_tool_still_requires_token(self):
        mock_db = MagicMock()
        result = asyncio.run(execute_tool(
            mock_db, "", "delete_project",
            {"id": "p2"},
            permission_pack="trusted_local_maintenance",
        ))
        self.assertTrue(result.is_error)
        parsed = json.loads(result.content[0]["text"])
        self.assertEqual(parsed["status"], "denied")
        self.assertEqual(parsed["reason"], "confirmation_required")


if __name__ == "__main__":
    unittest.main()
