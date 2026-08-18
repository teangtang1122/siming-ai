"""Tests for user-triggered MCP client configuration for local CLI providers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.external_agent import mcp_auto_config, mcp_server_spec


class McpAutoConfigTest(unittest.TestCase):
    def test_frozen_mcp_server_uses_siming_home_instead_of_caller_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SIMING_HOME": str(Path(temp_dir) / "siming-home")},
        ), patch.object(mcp_server_spec.sys, "frozen", True, create=True):
            server = mcp_auto_config._resolve_moshu_mcp_server(
                permission_pack="auto",
            )

        self.assertEqual(server["mode"], "exe")
        self.assertEqual(
            Path(server["cwd"]),
            (Path(temp_dir) / "siming-home").resolve(),
        )
        self.assertEqual(
            server["args"],
            ["--mcp-server", "--permission-pack", "auto"],
        )

    def test_scan_is_read_only_and_requires_no_configuration_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_config = root / "codex" / "config.toml"
            codex_config.parent.mkdir(parents=True)
            codex_config.write_text('[mcp_servers.siming]\ncommand = "python"\n', encoding="utf-8")

            def paths(provider: str):
                return [codex_config] if provider == "codex_cli" else [root / provider / "missing.json"]

            with patch("app.services.external_agent.mcp_auto_config._candidate_config_paths", side_effect=paths), patch(
                "app.services.external_agent.mcp_auto_config._resolve_command", return_value=None
            ), patch("app.services.external_agent.mcp_auto_config.cursor_command", return_value=None), patch(
                "app.services.external_agent.mcp_auto_config.hermes_command", return_value=None
            ), patch("app.services.external_agent.mcp_auto_config._read_transaction", return_value=None), patch(
                "app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider"
            ) as configure:
                result = mcp_auto_config.scan_cli_integrations()

            configure.assert_not_called()
            self.assertEqual(result["detected_count"], 1)
            self.assertEqual(result["clients"][0]["provider"], "codex_cli")
            self.assertTrue(result["clients"][0]["configured"])
            self.assertEqual(codex_config.read_text(encoding="utf-8"), '[mcp_servers.siming]\ncommand = "python"\n')

    def test_scan_detects_siming_after_other_json_mcp_servers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "opencode.json"
            config_path.write_text(json.dumps({
                "mcp": {
                    "other": {"type": "local", "command": ["other"]},
                    "siming": {"type": "local", "command": ["python", "server.py"]},
                },
                "permission": "ask",
            }), encoding="utf-8")

            self.assertTrue(mcp_auto_config._configuration_marker_present(config_path))

    def test_permission_wildcard_alone_does_not_count_as_an_mcp_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(json.dumps({
                "permissions": {"allow": ["mcp__siming__*"]},
            }), encoding="utf-8")

            self.assertFalse(mcp_auto_config._configuration_marker_present(settings_path))

    def test_unchanged_configuration_reports_no_change_and_no_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "opencode.json"
            original = b'{"mcp":{"siming":{"type":"local"}}}\n'
            config_path.write_bytes(original)
            with patch.dict(os.environ, {"SIMING_HOME": str(root / "siming")}), patch(
                "app.services.external_agent.mcp_auto_config._candidate_config_paths",
                return_value=[config_path],
            ), patch(
                "app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider",
                return_value={
                    "enabled": True,
                    "provider": "opencode_cli",
                    "status": "configured",
                    "detail": "already configured",
                },
            ):
                configured = mcp_auto_config.configure_cli_integration("opencode_cli")

            self.assertFalse(configured["changed"])
            self.assertFalse(configured["can_restore"])
            self.assertEqual(config_path.read_bytes(), original)

    def test_explicit_codex_configuration_can_restore_a_new_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "codex"
            siming_home = root / "siming"
            with patch.dict(os.environ, {
                "CODEX_HOME": str(config_dir),
                "SIMING_HOME": str(siming_home),
                "MOSHU_DISABLE_AUTO_MCP_SETUP": "",
            }), patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="codex.cmd"):
                configured = mcp_auto_config.configure_cli_integration("codex_cli")
                restored = mcp_auto_config.restore_cli_integration("codex_cli")

            self.assertEqual(configured["status"], "configured")
            self.assertTrue(configured["can_restore"])
            self.assertEqual(restored["status"], "restored")
            self.assertFalse((config_dir / "config.toml").exists())

    def test_partial_configuration_failure_still_keeps_a_restore_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "codex" / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('model = "before"\n', encoding="utf-8")

            def fail_after_write(*_args, **_kwargs):
                config_path.write_text('model = "partially changed"\n', encoding="utf-8")
                raise RuntimeError("writer stopped")

            with patch.dict(os.environ, {"SIMING_HOME": str(root / "siming")}), patch(
                "app.services.external_agent.mcp_auto_config._candidate_config_paths",
                return_value=[config_path],
            ), patch(
                "app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider",
                side_effect=fail_after_write,
            ):
                configured = mcp_auto_config.configure_cli_integration("codex_cli")
                restored = mcp_auto_config.restore_cli_integration("codex_cli")

            self.assertEqual(configured["status"], "error")
            self.assertTrue(configured["can_restore"])
            self.assertEqual(restored["status"], "restored")
            self.assertEqual(config_path.read_text(encoding="utf-8"), 'model = "before"\n')

    def test_restore_failure_compensates_files_back_to_the_pre_restore_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_path = root / "cli" / "first.json"
            second_path = root / "cli" / "second.json"
            first_path.parent.mkdir(parents=True)
            first_path.write_bytes(b"original-a")
            second_path.write_bytes(b"original-b")

            def configure_two_files(*_args, **_kwargs):
                first_path.write_bytes(b"configured-a")
                second_path.write_bytes(b"configured-b")
                return {
                    "enabled": True,
                    "provider": "codex_cli",
                    "status": "configured",
                    "detail": "configured",
                }

            with patch.dict(os.environ, {"SIMING_HOME": str(root / "siming")}), patch(
                "app.services.external_agent.mcp_auto_config._candidate_config_paths",
                return_value=[first_path, second_path],
            ), patch(
                "app.services.external_agent.mcp_auto_config.auto_configure_mcp_for_provider",
                side_effect=configure_two_files,
            ):
                configured = mcp_auto_config.configure_cli_integration("codex_cli")
                real_replace = mcp_auto_config._replace_file_bytes
                failed_once = False

                def fail_once_while_restoring_second(path: Path, content: bytes):
                    nonlocal failed_once
                    if path.name == second_path.name and content == b"original-b" and not failed_once:
                        failed_once = True
                        raise OSError("simulated restore failure")
                    real_replace(path, content)

                with patch(
                    "app.services.external_agent.mcp_auto_config._replace_file_bytes",
                    side_effect=fail_once_while_restoring_second,
                ):
                    restored = mcp_auto_config.restore_cli_integration("codex_cli")

            self.assertEqual(configured["status"], "configured")
            self.assertEqual(restored["status"], "error")
            self.assertTrue(restored["can_restore"])
            self.assertEqual(first_path.read_bytes(), b"configured-a")
            self.assertEqual(second_path.read_bytes(), b"configured-b")

    def test_restore_refuses_to_overwrite_cli_changes_made_after_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "codex"
            siming_home = root / "siming"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.toml"
            config_path.write_text('model = "before"\n', encoding="utf-8")
            with patch.dict(os.environ, {
                "CODEX_HOME": str(config_dir),
                "SIMING_HOME": str(siming_home),
                "MOSHU_DISABLE_AUTO_MCP_SETUP": "",
            }), patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="codex.cmd"):
                configured = mcp_auto_config.configure_cli_integration("codex_cli")
                config_path.write_text(config_path.read_text(encoding="utf-8") + 'model_reasoning_effort = "high"\n', encoding="utf-8")
                restored = mcp_auto_config.restore_cli_integration("codex_cli")

            self.assertEqual(configured["status"], "configured")
            self.assertEqual(restored["status"], "conflict")
            self.assertIn('model_reasoning_effort = "high"', config_path.read_text(encoding="utf-8"))

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
            # Registering the MCP server must not create or relax Claude's
            # separate global permission settings.
            self.assertFalse(settings_path.exists())

    def test_claude_config_preserves_existing_permission_settings(self):
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            original = {
                "theme": "dark",
                "permissions": {"allow": ["Bash(git *)"], "defaultMode": "default"},
                "skipDangerousModePermissionPrompt": False,
            }
            settings_path.write_text(json.dumps(original), encoding="utf-8")

            with patch.dict(os.environ, {"MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="claude"):
                    with patch("app.services.external_agent.mcp_auto_config.subprocess.run", return_value=completed):
                        with patch("app.services.external_agent.mcp_auto_config._claude_settings_path", return_value=settings_path):
                            result = mcp_auto_config.auto_configure_mcp_for_provider("claude_cli", cli_command="claude")

            self.assertEqual(result["status"], "configured")
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings, original)

    def test_claude_config_does_not_expand_legacy_permission_entries(self):
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
            self.assertNotIn("mcp__siming__*", allow)
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
            self.assertNotIn("permission", config)
            self.assertIn("siming", config["mcp"])
            self.assertIn("--permission-pack", config["mcp"]["siming"]["command"])
            self.assertEqual(
                config["mcp"]["siming"]["timeout"],
                mcp_auto_config.OPENCODE_MCP_TIMEOUT_MS,
            )

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
                    "permission": "ask",
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
            self.assertEqual(
                config["mcp"]["siming"]["timeout"],
                mcp_auto_config.OPENCODE_MCP_TIMEOUT_MS,
            )
            # Other settings preserved
            self.assertEqual(config["theme"], "dark")
            self.assertEqual(config["permission"], "ask")
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
            self.assertNotIn("permission", config)
            self.assertEqual(config["mcp"]["siming"]["type"], "local")
            self.assertTrue(config["mcp"]["siming"]["enabled"])
            self.assertIn("--permission-pack", config["mcp"]["siming"]["command"])

    def test_codex_config_does_not_relax_global_security_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            with patch.dict(os.environ, {"CODEX_HOME": str(config_dir), "MOSHU_DISABLE_AUTO_MCP_SETUP": ""}):
                with patch("app.services.external_agent.mcp_auto_config._resolve_command", return_value="codex.cmd"):
                    result = mcp_auto_config.auto_configure_mcp_for_provider("codex_cli")

            self.assertEqual(result["status"], "configured")
            text = (config_dir / "config.toml").read_text(encoding="utf-8")
            self.assertNotIn("approval_policy", text)
            self.assertNotIn("sandbox_mode", text)
            self.assertIn("[mcp_servers.siming]", text)

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
                            created = mcp_auto_config.ensure_detected_local_cli_model_configs(db, explicit_consent=True)

        self.assertEqual(created, ["mimocode_cli"])
        added = db.add.call_args.args[0]
        self.assertEqual(added.provider, "mimocode_cli")
        self.assertEqual(added.cli_command, "mimo.cmd")
        self.assertEqual(added.default_model, "xiaomi/mimo-v2.5-pro")
        self.assertEqual(added.readiness_status, "detected")
        self.assertNotIn("--dangerously-skip-permissions", added.cli_args)
        self.assertEqual(json.loads(added.cli_args), ["run", "{prompt}"])
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
                        mcp_auto_config.ensure_detected_local_cli_model_configs(db, explicit_consent=True)

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

        migrated = mcp_auto_config.migrate_legacy_external_agent_defaults(db, explicit_consent=True)

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

        migrated = mcp_auto_config.migrate_legacy_external_agent_defaults(db, explicit_consent=True)

        self.assertTrue(migrated)
        self.assertIn("kilocode", settings.trusted_local_clients)
        self.assertIn("hermes", settings.trusted_local_clients)
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()

def test_opencode_configuration_accepts_managed_command_outside_path():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        managed_command = root / "managed" / "opencode.exe"
        managed_command.parent.mkdir(parents=True)
        managed_command.write_bytes(b"managed-opencode")
        config_path = root / "config" / "opencode.json"
        server = {
            "command": "python",
            "args": ["-m", "app.mcp.server", "--permission-pack", "auto"],
        }
        with patch(
            "app.services.external_agent.mcp_auto_config._opencode_config_path",
            return_value=config_path,
        ), patch(
            "app.services.external_agent.mcp_auto_config.shutil.which",
            return_value=None,
        ):
            result = mcp_auto_config._configure_opencode(
                server,
                cli_command=str(managed_command),
            )

        assert result["status"] == "configured"
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["mcp"]["siming"]["enabled"] is True
        assert saved["mcp"]["siming"]["command"][:2] == ["python", "-m"]
