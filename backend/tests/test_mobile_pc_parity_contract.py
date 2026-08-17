from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_mobile_pc_parity_contract_is_source_backed_and_documented() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check-mobile-pc-parity.py"),
            "--root",
            str(ROOT),
            "--check-doc",
            "docs/mobile-pc-parity.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
