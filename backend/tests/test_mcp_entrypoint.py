"""Tests for the MCP stdio server entrypoint."""
import subprocess
import sys
import os
import unittest

# Path to the entrypoint script
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "moshu-mcp-server.py")


class EntrypointHelpTest(unittest.TestCase):
    """Verify the entrypoint accepts --help and exits cleanly."""

    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, _SCRIPT, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"Exit code {result.returncode}: {result.stderr}")

    def test_help_contains_description(self):
        result = subprocess.run(
            [sys.executable, _SCRIPT, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("MCP", result.stdout)
        self.assertIn("stdio", result.stdout.lower())

    def test_help_contains_project_id(self):
        result = subprocess.run(
            [sys.executable, _SCRIPT, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--project-id", result.stdout)


class EntrypointBadArgsTest(unittest.TestCase):
    """Verify the entrypoint rejects invalid arguments."""

    def test_unknown_arg_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, _SCRIPT, "--invalid-flag"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
