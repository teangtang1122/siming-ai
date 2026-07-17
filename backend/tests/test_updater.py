"""Tests for the explicit, verified packaged-app update flow."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import updater
from app.updater import is_newer_version


class UpdaterVersionTestCase(unittest.TestCase):
    def test_semver_comparison(self):
        self.assertTrue(is_newer_version("0.1.2", "0.1.1"))
        self.assertTrue(is_newer_version("v1.0.0", "0.9.9"))
        self.assertTrue(is_newer_version("3.0.0-alpha.2", "3.0.0-alpha.1"))
        self.assertTrue(is_newer_version("3.0.0-alpha.3", "3.0.0-alpha.2"))
        self.assertTrue(is_newer_version("3.0.0-beta.1", "3.0.0-alpha.9"))
        self.assertTrue(is_newer_version("3.0.0", "3.0.0-rc.1"))
        self.assertFalse(is_newer_version("3.0.0-alpha.1", "3.0.0"))
        self.assertFalse(is_newer_version("0.1.1", "0.1.1"))
        self.assertFalse(is_newer_version("0.1.0", "0.1.1"))
        self.assertFalse(is_newer_version("", "0.1.1"))

    def test_accepts_only_siming_exe_name(self):
        self.assertIn("siming.exe", updater.COMPATIBLE_EXE_NAMES)
        self.assertNotIn("moshu.exe", updater.COMPATIBLE_EXE_NAMES)
        self.assertNotIn("novelwritingagent.exe", updater.COMPATIBLE_EXE_NAMES)

    @patch("app.updater._request")
    @patch("app.updater._request_json")
    def test_github_manifest_prefers_siming_asset(self, mock_request_json, mock_request):
        mock_request_json.return_value = {
            "tag_name": "v0.1.2",
            "html_url": "https://github.com/example/repo/releases/tag/v0.1.2",
            "assets": [
                {"name": "NovelWritingAgent.exe", "browser_download_url": "https://example.test/legacy.exe"},
                {"name": "Moshu.exe", "browser_download_url": "https://example.test/moshu.exe"},
                {"name": "Siming.exe", "browser_download_url": "https://example.test/siming.exe"},
                {"name": "sha256.txt", "browser_download_url": "https://example.test/sha256.txt"},
            ],
        }
        mock_request.return_value = b"abc  Siming.exe\n"

        manifest = updater._manifest_from_github_release("example/repo")

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["version"], "0.1.2")
        self.assertEqual(manifest["download_url"], "https://example.test/siming.exe")

    @patch("app.updater._request_json")
    def test_github_manifest_requires_siming_asset(self, mock_request_json):
        mock_request_json.return_value = {
            "tag_name": "v0.1.2",
            "assets": [
                {"name": "NovelWritingAgent.exe", "browser_download_url": "https://example.test/legacy.exe"},
            ],
        }

        manifest = updater._manifest_from_github_release("example/repo")

        self.assertIsNone(manifest)

    @patch("app.updater._request")
    @patch("app.updater._request_json")
    def test_preview_channel_selects_latest_prerelease(
        self,
        mock_request_json,
        mock_request,
    ):
        mock_request_json.return_value = [
            {
                "tag_name": "v3.0.0-alpha.1",
                "prerelease": True,
                "draft": False,
                "assets": [
                    {
                        "name": "Siming.exe",
                        "browser_download_url": "https://example.test/a1.exe",
                    }
                ],
            },
            {
                "tag_name": "v2.9.1",
                "prerelease": False,
                "draft": False,
                "assets": [
                    {
                        "name": "Siming.exe",
                        "browser_download_url": "https://example.test/stable.exe",
                    }
                ],
            },
        ]
        mock_request.return_value = b""

        manifest = updater._manifest_from_github_release(
            "example/repo",
            "preview",
        )

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["version"], "3.0.0-alpha.1")
        self.assertEqual(manifest["download_url"], "https://example.test/a1.exe")

    def test_prerelease_build_defaults_to_preview_channel(self):
        self.assertEqual(
            updater.default_update_channel("3.0.0-alpha.1"),
            "preview",
        )
        self.assertEqual(updater.default_update_channel("3.0.0"), "stable")

    @patch.dict("os.environ", {"SIMING_DISABLE_UPDATE": "1"}, clear=True)
    def test_siming_disable_update_env_var(self):
        self.assertIsNone(updater.find_latest_update())

    @patch("app.updater._request_json")
    def test_url_manifest_requires_download_url(self, mock_request_json):
        mock_request_json.return_value = {
            "version": "0.1.2",
            "legacy_download_url": "https://example.test/NovelWritingAgent.exe",
            "sha256": "",
        }

        manifest = updater._manifest_from_url("https://example.test/update.json")

        self.assertIsNone(manifest)

    def test_silent_update_compatibility_shim_never_downloads_or_installs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(updater.apply_update_if_available(Path(temp_dir)))

    def test_download_requires_release_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = {
                "version": "9.9.9",
                "download_url": "https://example.test/Siming.exe",
                "sha256": "",
                "source": "https://example.test/release",
            }
            with patch("app.updater.find_latest_update", return_value=manifest):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    updater.download_and_stage_update(Path(temp_dir))

    def test_download_stages_only_after_hash_and_signature_checks(self):
        content = b"signed test executable"
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {
            "version": "9.9.9",
            "download_url": "https://example.test/Siming.exe",
            "sha256": checksum,
            "source": "https://example.test/release",
        }

        def write_download(_url, target, timeout=120):
            del timeout
            Path(target).write_bytes(content)

        signature = {"valid": True, "status": "Valid", "subject": "CN=Siming", "thumbprint": "ABC"}
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch("app.updater.find_latest_update", return_value=manifest), patch(
                "app.updater._download_to_file", side_effect=write_download
            ), patch("app.updater._require_valid_signature", return_value=signature) as verify_signature:
                result = updater.download_and_stage_update(home)

            staged = updater._read_staged_update(home)
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["staged_update"]["ready_to_install"])
            self.assertEqual(staged["version"], "9.9.9")
            self.assertEqual(staged["sha256"], checksum)
            self.assertTrue(Path(staged["path"]).is_file())
            self.assertGreaterEqual(verify_signature.call_count, 2)

    def test_failed_signature_removes_downloaded_executable(self):
        content = b"untrusted executable"
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {
            "version": "9.9.9",
            "download_url": "https://example.test/Siming.exe",
            "sha256": checksum,
            "source": "https://example.test/release",
        }

        def write_download(_url, target, timeout=120):
            del timeout
            Path(target).write_bytes(content)

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch("app.updater.find_latest_update", return_value=manifest), patch(
                "app.updater._download_to_file", side_effect=write_download
            ), patch("app.updater._require_valid_signature", side_effect=RuntimeError("untrusted")):
                with self.assertRaisesRegex(RuntimeError, "untrusted"):
                    updater.download_and_stage_update(home)

            self.assertFalse((home / "updates" / "Siming-9.9.9.exe").exists())
            self.assertIsNone(updater._read_staged_update(home))

    def test_install_uses_verified_new_executable_as_helper_without_powershell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "Siming.exe"
            staged = root / "updates" / "Siming-9.9.9.exe"
            current.write_bytes(b"old")
            staged.parent.mkdir()
            staged.write_bytes(b"new")
            staged_payload = {
                "version": "9.9.9",
                "path": str(staged),
                "sha256": "a" * 64,
                "signature": {"valid": True, "status": "Valid"},
            }
            with patch("app.updater._current_packaged_executable", return_value=current), patch(
                "app.updater._validate_staged_update", return_value=staged_payload
            ), patch("app.updater.subprocess.Popen") as popen:
                result = updater.schedule_staged_update_install(root)

            self.assertTrue(result["restart_scheduled"])
            command = popen.call_args.args[0]
            self.assertTrue(Path(command[0]).samefile(staged))
            self.assertIn("--apply-staged-update", command)
            self.assertEqual(command[command.index("--expected-sha256") + 1], "a" * 64)
            self.assertNotIn("powershell.exe", [part.lower() for part in command])

    def test_updater_source_does_not_contain_execution_policy_bypass(self):
        source = Path(updater.__file__).read_text(encoding="utf-8")
        banned = " ".join(("Execution" + "Policy", "By" + "pass"))
        self.assertNotIn(banned, source)
        self.assertNotIn("powershell.exe", source.lower())


if __name__ == "__main__":
    unittest.main()
