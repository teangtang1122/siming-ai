"""Packaging guardrails for dynamically loaded application modules."""
from __future__ import annotations

from pathlib import Path

from app.services.workspace.dynamic_modules import LEGACY_HANDLER_MODULES


def test_build_collects_dynamic_workspace_tool_modules():
    root = Path(__file__).resolve().parents[2]
    build_script = (root / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert "from app.services.workspace.dynamic_modules import LEGACY_HANDLER_MODULES" in build_script
    assert '$PyInstallerArgs.Insert($EntryPointIndex, "--hidden-import")' in build_script
    assert "app.services.workspace.tools.external_agent" in LEGACY_HANDLER_MODULES
    assert len(LEGACY_HANDLER_MODULES) == len(set(LEGACY_HANDLER_MODULES))
