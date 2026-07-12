#!/usr/bin/env python3
"""Run backend test modules in isolated Python processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    tests_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
    test_files = sorted(tests_dir.glob("test_*.py"))
    if not test_files:
        print(f"No backend tests found in {tests_dir}", file=sys.stderr)
        return 2

    for index, test_file in enumerate(test_files, start=1):
        print(f"\n[{index}/{len(test_files)}] {test_file}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-q",
                "--tb=short",
                "--disable-warnings",
            ],
            check=False,
        )
        if result.returncode:
            return result.returncode

    print(f"\nAll {len(test_files)} backend test modules passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
