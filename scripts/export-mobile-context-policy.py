#!/usr/bin/env python3
"""Export the PC writing-context policy consumed by Android standalone mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.mobile_context_policy import portable_context_policy  # noqa: E402

DEFAULT_OUTPUT = (
    ROOT
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "assets"
    / "pc_context_manifest_policy.json"
)


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_policy() -> dict[str, Any]:
    payload = portable_context_policy("writing")
    payload["source_sha256"] = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return payload


def render_policy(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = render_policy(build_policy())
    if args.check:
        actual = output.read_text(encoding="utf-8") if output.is_file() else ""
        if actual != expected:
            print(f"generated Android context policy is stale: {output}", file=sys.stderr)
            return 1
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
