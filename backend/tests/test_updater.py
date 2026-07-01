"""Tests for packaged app updater helpers."""

import unittest
from unittest.mock import patch

from app import updater
from app.updater import is_newer_version


class UpdaterVersionTestCase(unittest.TestCase):
    def test_semver_comparison(self):
        self.assertTrue(is_newer_version("0.1.2", "0.1.1"))
        self.assertTrue(is_newer_version("v1.0.0", "0.9.9"))
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


if __name__ == "__main__":
    unittest.main()
