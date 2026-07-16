"""Release packaging must include modules loaded only by migration scripts."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_packager_includes_dynamic_database_migration_module():
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")

    assert '"--hidden-import", "app.database.migrations"' in script
    assert '"--add-data", "$(Join-Path $BackendDir \'alembic\')' in script
    assert "PackagerPythonVersion -ne $BuildPythonVersion" in script
    assert 'import tkinter' in script
