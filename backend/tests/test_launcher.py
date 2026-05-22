"""Tests for packaged launcher data-directory compatibility."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher


class LauncherDataDirectoryTestCase(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
