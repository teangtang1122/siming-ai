"""Tests for tool registry linter."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util
_script_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-tool-registry.py")
_spec = importlib.util.spec_from_file_location("check_tool_registry", _script_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
check_tool = _mod.check_tool
from app.services.workspace.registry import registry, ToolDef


class LinterTest(unittest.TestCase):
    """Verify linter checks."""

    def test_all_registered_tools_pass(self):
        """Every tool in the registry should pass the linter."""
        all_issues = []
        for name in registry.all_names():
            td = registry.get(name)
            if td:
                issues = check_tool(td)
                all_issues.extend(issues)
        self.assertEqual(all_issues, [], f"Linter issues: {all_issues}")

    def test_tool_without_description_fails(self):
        td = ToolDef(
            name="test_no_desc",
            description="",
            input_schema={"q": {"type": "string"}},
            tool_type="read",
        )
        issues = check_tool(td)
        self.assertTrue(any("description" in i for i in issues))

    def test_tool_without_handler_fails(self):
        td = ToolDef(
            name="test_no_handler",
            description="A test tool",
            input_schema={"q": {"type": "string"}},
            tool_type="read",
            handler=None,
        )
        issues = check_tool(td)
        self.assertTrue(any("handler" in i for i in issues))

    def test_invalid_risk_level_fails(self):
        td = ToolDef(
            name="test_bad_risk",
            description="A test tool",
            input_schema={"q": {"type": "string"}},
            tool_type="read",
            risk_level="unknown",
            handler=lambda: None,
        )
        issues = check_tool(td)
        self.assertTrue(any("risk_level" in i for i in issues))

    def test_secret_tool_exposed_fails(self):
        td = ToolDef(
            name="get_api_key",
            description="Get API key",
            input_schema={},
            tool_type="read",
            expose_to_mcp=True,
            handler=lambda: None,
        )
        issues = check_tool(td)
        self.assertTrue(any("secret" in i.lower() for i in issues))

    def test_empty_input_schema_passes(self):
        """Tools with no parameters have empty input_schema — this is valid."""
        td = ToolDef(
            name="test_empty_schema",
            description="A test tool",
            input_schema={},
            tool_type="read",
            handler=lambda: None,
        )
        issues = check_tool(td)
        # Should not have input_schema issue
        self.assertFalse(any("input_schema" in i for i in issues))


class LinterScriptTest(unittest.TestCase):
    """Verify the linter script exists and runs."""

    def test_script_exists(self):
        script_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-tool-registry.py")
        self.assertTrue(os.path.exists(script_path))

    def test_script_compiles(self):
        import py_compile
        script_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "check-tool-registry.py")
        py_compile.compile(script_path, doraise=True)


if __name__ == "__main__":
    unittest.main()
