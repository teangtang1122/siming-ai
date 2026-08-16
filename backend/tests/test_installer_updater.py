"""Tests for the installer-aware Windows update path."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import installer_updater


class InstallerUpdaterTestCase(unittest.TestCase):
    @patch("app.installer_updater.legacy._request")
    def test_release_uses_setup_asset_and_its_checksum(self, mock_request):
        mock_request.return_value = (b"b" * 64) + b"  Siming-Setup.exe\n"
        release = {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/example/repo/releases/tag/v9.9.9",
            "assets": [
                {
                    "name": "Siming-Setup.exe",
                    "browser_download_url": "https://example.test/Siming-Setup.exe",
                },
                {
                    "name": "Siming-Setup.sha256",
                    "browser_download_url": "https://example.test/Siming-Setup.sha256",
                },
            ],
        }

        manifest = installer_updater._manifest_from_release_payload(
            "example/repo",
            release,
        )

        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["asset_name"], "Siming-Setup.exe")
        self.assertEqual(manifest["install_mode"], "installer")
        self.assertEqual(
            manifest["download_url"],
            "https://example.test/Siming-Setup.exe",
        )
        self.assertEqual(manifest["sha256"], "b" * 64)

    def test_unsigned_installer_update_is_allowed_with_matching_sha256(self):
        content = b"unsigned setup"
        checksum = hashlib.sha256(content).hexdigest()
        manifest = {
            "version": "9.9.9",
            "download_url": "https://example.test/Siming-Setup.exe",
            "sha256": checksum,
            "source": "https://example.test/release",
            "asset_name": "Siming-Setup.exe",
            "install_mode": "installer",
            "migration": False,
        }

        def write_download(_url, target, timeout=120):
            del timeout
            Path(target).write_bytes(content)

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with patch(
                "app.installer_updater.find_latest_update",
                return_value=manifest,
            ), patch(
                "app.installer_updater.legacy._download_to_file",
                side_effect=write_download,
            ), patch(
                "app.installer_updater.legacy._require_valid_signature",
                side_effect=RuntimeError("signature should not be checked"),
            ) as require_signature:
                result = installer_updater.download_and_stage_update(home)

            staged = installer_updater.legacy._read_staged_update(home)
            self.assertTrue(result["downloaded"])
            self.assertTrue(result["staged_update"]["ready_to_install"])
            self.assertFalse(result["signature_verification_required"])
            self.assertIsNone(result["staged_update"]["signature"])
            self.assertEqual(staged["sha256"], checksum)
            require_signature.assert_not_called()

    def test_staged_installer_still_rejects_sha256_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            updates = home / "updates"
            updates.mkdir()
            setup = updates / "Siming-Setup-9.9.9.exe"
            setup.write_bytes(b"tampered")
            installer_updater.legacy._write_staged_update(
                home,
                {
                    "version": "9.9.9",
                    "path": str(setup),
                    "sha256": hashlib.sha256(b"original").hexdigest(),
                    "install_mode": "installer",
                    "signature": None,
                },
            )

            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                installer_updater._validate_staged_update(home)

    def test_installed_layout_runs_setup_silently_in_same_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "Siming.exe"
            current.write_bytes(b"app")
            (root / installer_updater.INSTALL_MARKER).write_text("installed")
            setup = root / "updates" / "Siming-Setup-9.9.9.exe"
            setup.parent.mkdir()
            setup.write_bytes(b"setup")
            staged = {
                "version": "9.9.9",
                "path": str(setup),
                "sha256": "a" * 64,
                "signature": None,
                "install_mode": "installer",
            }
            with patch(
                "app.installer_updater.legacy._current_packaged_executable",
                return_value=current,
            ), patch(
                "app.installer_updater._validate_staged_update",
                return_value=staged,
            ), patch("app.installer_updater.subprocess.Popen") as popen:
                result = installer_updater.schedule_staged_update_install(root)

            command = popen.call_args.args[0]
            self.assertTrue(result["restart_scheduled"])
            self.assertFalse(result["migration"])
            self.assertIn("/VERYSILENT", command)
            self.assertIn("/SUPPRESSMSGBOXES", command)
            self.assertIn(f"/DIR={root}", command)

    def test_non_installed_packaged_layout_keeps_installer_interactive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "Siming.exe"
            current.write_bytes(b"app")
            setup = root / "updates" / "Siming-Setup-9.9.9.exe"
            setup.parent.mkdir()
            setup.write_bytes(b"setup")
            staged = {
                "version": "9.9.9",
                "path": str(setup),
                "sha256": "a" * 64,
                "signature": None,
                "install_mode": "installer",
            }
            with patch(
                "app.installer_updater.legacy._current_packaged_executable",
                return_value=current,
            ), patch(
                "app.installer_updater._validate_staged_update",
                return_value=staged,
            ), patch("app.installer_updater.subprocess.Popen") as popen:
                result = installer_updater.schedule_staged_update_install(root)

            command = popen.call_args.args[0]
            self.assertTrue(result["migration"])
            self.assertIn("/SP-", command)
            self.assertNotIn("/VERYSILENT", command)
            self.assertFalse(any(part.startswith("/DIR=") for part in command))


if __name__ == "__main__":
    unittest.main()