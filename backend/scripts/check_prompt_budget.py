#!/usr/bin/env python3
"""Verify the four core AI flows remain materially smaller than 2.9."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.assistant.infrastructure.runtime import compile_prompt_catalog  # noqa: E402

BASELINE_PATH = BACKEND / "prompt-volume-baseline.json"


def prompt_volume_report() -> dict[str, Any]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    compiled = compile_prompt_catalog()
    rows: dict[str, dict[str, Any]] = {}
    baseline_total = 0
    current_total = 0
    for name, definition in baseline["flows"].items():
        baseline_chars = int(definition["baseline_chars"])
        spec_id = str(definition["spec_id"])
        current_chars = len(compiled[spec_id].template)
        baseline_total += baseline_chars
        current_total += current_chars
        rows[name] = {
            "spec_id": spec_id,
            "baseline_chars": baseline_chars,
            "current_chars": current_chars,
            "reduction_percent": round(
                (1 - current_chars / baseline_chars) * 100,
                2,
            ),
        }
    reduction = round((1 - current_total / baseline_total) * 100, 2)
    return {
        "source_tag": baseline["source_tag"],
        "minimum_reduction_percent": baseline["minimum_reduction_percent"],
        "baseline_chars": baseline_total,
        "current_chars": current_total,
        "reduction_percent": reduction,
        "flows": rows,
    }


def main() -> int:
    report = prompt_volume_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["reduction_percent"] < report["minimum_reduction_percent"])


if __name__ == "__main__":
    raise SystemExit(main())
