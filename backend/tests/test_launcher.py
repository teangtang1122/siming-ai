"""Tests for packaged launcher data-directory compatibility."""

import os
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher
from app.routers import config


class LauncherDataDirectoryTestCase(unittest.TestCase):
    def test_mcp_stdio_replaces_windowed_streams_with_inherited_pipes(self):
        stdin = io.StringIO()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(launcher.os, "name", "nt"), patch.object(
            launcher, "_open_inherited_windows_stream", side_effect=[stdin, stdout, stderr]
        ) as open_stream, patch.object(launcher, "_configure_stdio_utf8"):
            with patch.object(launcher.sys, "stdin", None), patch.object(
                launcher.sys, "stdout", None
            ), patch.object(launcher.sys, "stderr", None):
                launcher._ensure_mcp_stdio()

                self.assertIs(launcher.sys.stdin, stdin)
                self.assertIs(launcher.sys.stdout, stdout)
                self.assertIs(launcher.sys.stderr, stderr)

        self.assertEqual(
            [call.args for call in open_stream.call_args_list],
            [(-10, "r"), (-11, "w"), (-12, "w")],
        )

    def test_desktop_api_returns_selected_export_directory(self):
        class FakeWindow:
            def create_file_dialog(self, dialog_type, allow_multiple=False):
                self.call = (dialog_type, allow_multiple)
                return (r"C:\exports",)

        window = FakeWindow()
        api = launcher.DesktopApi()
        api.bind(window, "folder")

        self.assertEqual(api.select_export_directory(), r"C:\exports")
        self.assertEqual(window.call, ("folder", False))

    def test_uses_legacy_data_directory_when_current_database_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy = base / "NovelWritingAgent"
            legacy.mkdir()
            (legacy / "novel_agent.db").write_bytes(b"legacy database")

            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(base), "USERPROFILE": str(base)},
                clear=True,
            ):
                self.assertEqual(launcher._app_home(), legacy)

    def test_uses_moshu_home_when_explicitly_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "custom"
            with patch.dict("os.environ", {"MOSHU_HOME": str(home)}, clear=True):
                self.assertEqual(launcher._app_home(), home.resolve())

    def test_prepare_data_environment_sets_database_url_for_legacy_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy = base / "NovelWritingAgent"
            legacy.mkdir()
            (legacy / "novel_agent.db").write_bytes(b"legacy database")

            with patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(base), "USERPROFILE": str(base)},
                clear=True,
            ):
                home = launcher._prepare_data_environment()

                self.assertEqual(home, legacy)
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    f"sqlite:///{(legacy / 'novel_agent.db').as_posix()}",
                )
                self.assertEqual(os.environ["MOSHU_HOME"], str(legacy))
                self.assertEqual(os.environ["NOVEL_AGENT_HOME"], str(legacy))

    def test_browser_mode_is_persisted_and_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            launcher._save_launcher_settings(home, {"launch_mode": "browser"})

            self.assertEqual(launcher._saved_launch_mode(home), "browser")
            self.assertTrue(launcher._use_browser_mode(home))
            self.assertFalse(launcher._use_browser_mode(home, force_desktop=True))
            self.assertTrue(launcher._use_browser_mode(home, force_browser=True, force_desktop=True))

    def test_settings_api_and_launcher_share_the_same_launch_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(launch_mode="browser")
                )

            self.assertEqual(response.data["launch_mode"], "browser")
            self.assertIn(response.data["update_channel"], {"stable", "preview"})
            self.assertTrue(response.data["restart_required"])
            self.assertEqual(launcher._saved_launch_mode(home), "browser")

    def test_update_channel_is_saved_with_launcher_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(
                        update_channel="preview"
                    )
                )

            self.assertEqual(response.data["update_channel"], "preview")

    def test_gateway_launcher_settings_are_normalized_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.dict(os.environ, {"SIMING_HOME": str(home)}, clear=False):
                response = config.update_launcher_settings(
                    config.LauncherSettingsUpdateRequest(
                        gateway_enabled=True,
                        gateway_advertised_url="https://Siming.Example.ts.net/",
                        gateway_allowed_hosts="Siming.Example.ts.net, 192.168.1.20",
                    )
                )

            self.assertTrue(response.data["gateway_enabled"])
            self.assertEqual(
                response.data["gateway_advertised_url"],
                "https://siming.example.ts.net",
            )
            self.assertEqual(
                response.data["gateway_allowed_hosts"],
                "siming.example.ts.net,192.168.1.20",
            )
            saved = launcher._load_launcher_settings(home)
            self.assertNotIn("gateway_bootstrap_key", saved)

    def test_browser_mode_starts_local_server_without_creating_a_webview_window(self):
        class FakeThread:
            instances = []

            def __init__(self, *, target, daemon=False):
                self.target = target
                self.daemon = daemon
                self.started = False
                self.__class__.instances.append(self)

            def start(self):
                self.started = True

        class FakeEvent:
            def wait(self):
                return None

        browser_app = types.ModuleType("app.main")
        browser_app.app = object()
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch.object(launcher.sys, "argv", ["launcher.py", "--browser"]), patch(
                "launcher._find_free_port", return_value=9876
            ), patch("launcher._prepare_environment", return_value=home), patch(
                "launcher._wait_for_server", return_value=True
            ), patch("launcher._log"), patch("launcher.threading.Thread", FakeThread), patch(
                "launcher.threading.Event", FakeEvent
            ), patch("webbrowser.open") as open_browser, patch.dict(
                "sys.modules", {"app.main": browser_app}
            ):
                launcher.main()

        self.assertEqual(len(FakeThread.instances), 1)
        self.assertTrue(FakeThread.instances[0].started)
        open_browser.assert_called_once_with("http://127.0.0.1:9876/gui")

    def test_staged_update_helper_replaces_old_executable_and_restarts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            update_exe = root / "Siming-2.8.0.exe"
            target_exe = root / "Siming.exe"
            update_exe.write_bytes(b"verified update")
            target_exe.write_bytes(b"old release")
            args = [
                str(update_exe),
                "--apply-staged-update",
                "--update-target",
                str(target_exe),
                "--wait-pid",
                "1234",
                "--expected-sha256",
                "b" * 64,
            ]

            expected_sha256 = launcher._sha256_file(update_exe)
            args[args.index("--expected-sha256") + 1] = expected_sha256

            with patch.object(launcher.sys, "argv", args), patch.object(
                launcher.sys, "executable", str(update_exe)
            ), patch("launcher._wait_for_process_exit", return_value=True), patch("launcher.subprocess.Popen") as popen:
                launcher._apply_staged_update()

            self.assertEqual(target_exe.read_bytes(), b"verified update")
            self.assertTrue(Path(popen.call_args.args[0][0]).samefile(target_exe))

    def test_launcher_source_does_not_contain_execution_policy_bypass(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")
        banned = " ".join(("Execution" + "Policy", "By" + "pass"))
        self.assertNotIn(banned, source)

    def test_system_trust_is_configured_before_network_frameworks_are_imported(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")

        self.assertLess(
            source.index("SYSTEM_TRUST_STATUS = configure_system_trust()"),
            source.index("import uvicorn"),
        )


if __name__ == "__main__":
    unittest.main()
