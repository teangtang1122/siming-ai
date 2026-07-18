#!/usr/bin/env python3
"""Run deterministic backend architecture and style gates."""
from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RUFF_TARGETS = [
    "app/architecture",
    "app/bootstrap",
    "app/modules",
    "app/database/bootstrap.py",
    "app/database/schema_models.py",
    "app/core/numbers.py",
    "app/prompts/workspace_contract.py",
    "app/modules/assistant",
    "app/modules/creation",
    "app/modules/continuity",
    "app/services/scheduler/ports.py",
    "app/services/skills/tool_catalog.py",
    "app/services/workspace/idempotency.py",
    "app/services/workspace/scheduled_task_runner.py",
    "scripts",
    "tests/test_architecture_foundation.py",
    "tests/test_database_bootstrap.py",
    "tests/test_prompt_spec_compiler.py",
    "tests/test_tool_spec_catalog.py",
]


def _run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def _lint_imports_executable() -> str:
    suffix = ".exe" if sys.platform == "win32" else ""
    candidate = Path(sysconfig.get_path("scripts")) / f"lint-imports{suffix}"
    return str(candidate)


def main() -> int:
    _run([sys.executable, str(ROOT / "scripts" / "check-architecture.py")], cwd=ROOT)
    _run([sys.executable, "scripts/compile_prompts.py"], cwd=BACKEND)
    _run([sys.executable, "-m", "ruff", "check", *RUFF_TARGETS], cwd=BACKEND)
    _run([_lint_imports_executable()], cwd=BACKEND)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
