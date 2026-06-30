"""Tests for automatic MCP client configuration for local CLI providers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.external_agent import mcp_auto_config


class McpAutoConfigTest(unittest.TestCase):
    def test_codex_config_replaces_legacy_moshu_block_with_siming(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_path = config_dir / "config.toml"
            config_path.write_text(
                "\n".join([
                    '[profiles.default]',
                    'model = "gpt-5"',
                    "",
                    "[mcp_servers.other]",
                    'type = "stdio"',
                    'command = "other"',
                    "",
                    "[mcp_servers.moshu]",
                    'type = "stdio"',
                    'command = "old"',
                    'args = ["old"]',
                    "",
                    "[ui]",
                    'theme = "dark"',
                    "",
                ]),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config.shutil.which", return_value=None):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("codex_cli")

            self.assertEqual(result["status"], "configured")
            new_text = config_path.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.other]", new_text)
            self.assertIn("[ui]", new_text)
            self.assertIn("[mcp_servers.siming]", new_text)
            self.assertNotIn("[mcp_servers.moshu]", new_text)
            self.assertIn("--permission-pack", new_text)
            self.assertIn('"auto"', new_text)
            self.assertNotIn('command = "old"', new_text)
            self.assertTrue(list(config_dir.glob("config.toml.bak-*")))

    def test_claude_config_uses_remove_then_add(self):
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / ".claude" / "settings.json"
            with patch.dict(os.environ, {"MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="claude"):
                    with patch("app.services.external_agent.mcp_auto_config.subprocess.run", return_value=completed) as run:
                        with patch("app.services.external_agent.mcp_auto_config._claude_settings_path", return_value=settings_path):
                            result = mcp_auto_config.auto_configure_mcp_for_provider("claude_cli", cli_command="claude")

            self.assertEqual(result["status"], "configured")
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[0][:5], ["claude", "mcp", "remove", "-s", "user"])
            self.assertEqual(calls[1][:6], ["claude", "mcp", "remove", "-s", "user", "moshu"])
            self.assertEqual(calls[2][:7], ["claude", "mcp", "add", "-s", "user", "siming", "--"])
            self.assertIn("--permission-pack", calls[2])
            self.assertIn("auto", calls[2])
            # Verify permission was auto-added
            self.assertTrue(settings_path.exists())
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn("mcp__siming__*", settings.get("permissions", {}).get("allow", []))
            self.assertIn("mcp__moshu__*", settings.get("permissions", {}).get("allow", []))

    def test_claude_config_permission_added_to_existing_settings(self):
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({
                    "theme": "dark",
                    "permissions": {"allow": ["Bash(git *)"]},
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="claude"):
                    with patch("app.services.external_agent.mcp_auto_config.subprocess.run", return_value=completed):
                        with patch("app.services.external_agent.mcp_auto_config._claude_settings_path", return_value=settings_path):
                            result = mcp_auto_config.auto_configure_mcp_for_provider("claude_cli", cli_command="claude")

            self.assertEqual(result["status"], "configured")
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            allow = settings["permissions"]["allow"]
            # Existing entries preserved
            self.assertIn("Bash(git *)", allow)
            # Siming wildcard added; legacy wildcard remains allowed for existing clients
            self.assertIn("mcp__siming__*", allow)
            self.assertIn("mcp__moshu__*", allow)
            # Other settings preserved
            self.assertEqual(settings["theme"], "dark")

    def test_claude_config_permission_already_present_no_duplicate(self):
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({
                    "permissions": {"allow": ["mcp__moshu__*", "Bash(git *)"]},
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="claude"):
                    with patch("app.services.external_agent.mcp_auto_config.subprocess.run", return_value=completed):
                        with patch("app.services.external_agent.mcp_auto_config._claude_settings_path", return_value=settings_path):
                            mcp_auto_config.auto_configure_mcp_for_provider("claude_cli", cli_command="claude")

            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            allow = settings["permissions"]["allow"]
            self.assertIn("mcp__siming__*", allow)
            # No duplicate legacy entry added
            self.assertEqual(allow.count("mcp__moshu__*"), 1)

    def test_disabled_by_env(self):
        with patch.dict(os.environ, {"MOSHU_DISABLE_AUTO_MCP_SETUP": "1"}):
            result = mcp_auto_config.auto_configure_mcp_for_provider("claude_cli", cli_command="claude")
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["enabled"])

    def test_opencode_config_creates_new_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_path = config_dir / "opencode.json"

            with patch.dict(os.environ, {"OPENCODE_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config.shutil.which", return_value=None):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("opencode_cli")

            self.assertEqual(result["status"], "configured")
            self.assertTrue(config_path.exists())
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["permission"], "allow")
            self.assertIn("siming", config["mcp"])
            self.assertIn("--permission-pack", config["mcp"]["siming"]["command"])

    def test_opencode_config_preserves_existing_servers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_path = config_dir / "opencode.json"
            config_path.write_text(
                json.dumps({
                    "mcp": {
                        "other-server": {
                            "type": "local",
                            "command": ["other", "--flag"],
                        }
                    },
                    "theme": "dark",
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENCODE_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config.shutil.which", return_value=None):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("opencode_cli")

            self.assertEqual(result["status"], "configured")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            # Existing server preserved
            self.assertIn("other-server", config["mcp"])
            self.assertEqual(config["mcp"]["other-server"]["command"], ["other", "--flag"])
            # Siming added
            self.assertIn("siming", config["mcp"])
            # Other settings preserved
            self.assertEqual(config["theme"], "dark")
            self.assertEqual(config["permission"], "allow")
            # Backup created
            self.assertTrue(list(config_dir.glob("opencode.json.bak-*")))

    def test_opencode_config_migrates_existing_moshu_to_siming(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_path = config_dir / "opencode.json"
            config_path.write_text(
                json.dumps({
                    "mcp": {
                        "moshu": {
                            "type": "local",
                            "command": ["old-command", "old"],
                        }
                    }
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENCODE_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config.shutil.which", return_value=None):
                    mcp_auto_config.auto_configure_mcp_for_provider("opencode_cli")

            config = json.loads(config_path.read_text(encoding="utf-8"))
            # Old entry replaced under the new server name
            self.assertNotIn("moshu", config["mcp"])
            self.assertNotEqual(config["mcp"]["siming"]["command"][0], "old-command")
            self.assertIn("--permission-pack", config["mcp"]["siming"]["command"])

    def test_mimocode_config_uses_native_global_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            config_path = config_dir / "mimocode.json"
            with patch.dict(os.environ, {"MIMOCODE_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="mimo.cmd"):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("mimocode_cli")

            self.assertEqual(result["status"], "configured")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["permission"], "allow")
            self.assertEqual(config["mcp"]["siming"]["type"], "local")
            self.assertTrue(config["mcp"]["siming"]["enabled"])
            self.assertIn("--permission-pack", config["mcp"]["siming"]["command"])

    def test_codex_config_enables_noninteractive_trusted_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            with patch.dict(os.environ, {"CODEX_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="codex.cmd"):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("codex_cli")

            self.assertEqual(result["status"], "configured")
            text = (config_dir / "config.toml").read_text(encoding="utf-8")
            self.assertIn('approval_policy = "never"', text)
            self.assertIn('sandbox_mode = "danger-full-access"', text)

    def test_detected_cli_is_registered_as_model_provider(self):
        db = MagicMock()
        query = MagicMock()
        query.filter.return_value = query
        query.first.return_value = None
        db.query.return_value = query

        def resolve(_command, fallbacks):
            return "mimo.cmd" if "mimo.cmd" in fallbacks else None

        with patch("app.services.external_agent.mcp_auto_config._resolve_command", side_effect=resolve):
            with patch("app.services.external_agent.mcp_auto_config.cursor_command", return_value=None):
                with patch("app.services.external_agent.mcp_auto_config.hermes_command", return_value=None):
                    with patch("app.core.crypto.encrypt", return_value="encrypted"):
                        with patch(
                            "app.ai.local_cli_adapter.preferred_local_cli_model",
                            return_value="xiaomi/mimo-v2.5-pro",
                        ):
                            created = mcp_auto_config.ensure_detected_local_cli_model_configs(db)

        self.assertEqual(created, ["mimocode_cli"])
        added = db.add.call_args.args[0]
        self.assertEqual(added.provider, "mimocode_cli")
        self.assertEqual(added.cli_command, "mimo.cmd")
        self.assertEqual(added.default_model, "xiaomi/mimo-v2.5-pro")
        self.assertIn("--dangerously-skip-permissions", added.cli_args)
        db.commit.assert_called_once()

    def test_legacy_mimocode_placeholder_model_is_migrated(self):
        existing = MagicMock()
        existing.provider = "mimocode_cli"
        existing.default_model = "mimocode-cli"
        db = MagicMock()
        query = MagicMock()
        query.filter.return_value = query
        query.first.return_value = existing
        db.query.return_value = query

        def resolve(_command, fallbacks):
            return "mimo.cmd" if "mimo.cmd" in fallbacks else None

        with patch("app.services.external_agent.mcp_auto_config._resolve_command", side_effect=resolve):
            with patch("app.services.external_agent.mcp_auto_config.cursor_command", return_value=None):
                with patch("app.services.external_agent.mcp_auto_config.hermes_command", return_value=None):
                    with patch(
                        "app.ai.local_cli_adapter.preferred_local_cli_model",
                        return_value="xiaomi/mimo-v2.5-pro",
                    ):
                        mcp_auto_config.ensure_detected_local_cli_model_configs(db)

        self.assertEqual(existing.default_model, "xiaomi/mimo-v2.5-pro")
        db.commit.assert_called_once()

    def test_legacy_permission_defaults_are_migrated_once(self):
        settings = MagicMock()
        settings.trusted_local_enabled = True
        settings.trusted_local_clients = []
        settings.require_confirmation_for_writes = True
        settings.require_confirmation_for_destructive = True
        db = MagicMock()
        query = MagicMock()
        query.first.return_value = settings
        db.query.return_value = query

        migrated = mcp_auto_config.migrate_legacy_external_agent_defaults(db)

        self.assertTrue(migrated)
        self.assertIn("mimocode", settings.trusted_local_clients)
        self.assertIn("qwen-code", settings.trusted_local_clients)
        self.assertIn("openclaw", settings.trusted_local_clients)
        self.assertFalse(settings.require_confirmation_for_writes)
        self.assertFalse(settings.require_confirmation_for_destructive)
        db.commit.assert_called_once()

    def test_previous_default_client_list_is_extended_without_overwriting_custom_lists(self):
        settings = MagicMock()
        settings.trusted_local_enabled = True
        settings.trusted_local_clients = [
            "claude-code",
            "codex",
            "opencode",
            "mimocode",
            "cursor",
            "trae",
        ]
        settings.require_confirmation_for_writes = False
        settings.require_confirmation_for_destructive = False
        db = MagicMock()
        query = MagicMock()
        query.first.return_value = settings
        db.query.return_value = query

        migrated = mcp_auto_config.migrate_legacy_external_agent_defaults(db)

        self.assertTrue(migrated)
        self.assertIn("kilocode", settings.trusted_local_clients)
        self.assertIn("hermes", settings.trusted_local_clients)
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
