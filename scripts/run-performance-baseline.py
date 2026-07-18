#!/usr/bin/env python3
"""Measure deterministic Siming startup paths and enforce generous RC budgets."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("SIMING_DISABLE_UPDATE", "1")
os.environ.setdefault("SIMING_ENABLE_LOCAL_RUNTIME", "0")
os.environ.setdefault("MOSHU_DISABLE_AUTO_MCP_SETUP", "1")

BUDGETS_MS = {
    "tool_and_prompt_catalog": 10_000.0,
    "app_factory_and_openapi": 15_000.0,
    "fresh_database_bootstrap": 30_000.0,
    "total": 45_000.0,
}


def _measure(action: Callable[[], dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    details = action()
    return round((time.perf_counter() - started) * 1000, 3), details


def _catalog_baseline() -> dict[str, Any]:
    from app.modules.assistant.infrastructure.runtime import compile_prompt_catalog
    from app.services.workspace.registry import registry

    prompts = compile_prompt_catalog(known_tools=registry.all_names())
    return {"tool_count": len(registry.all_names()), "prompt_count": len(prompts)}


def _application_baseline() -> dict[str, Any]:
    from app.bootstrap import create_app

    app = create_app(run_startup=False)
    schema = app.openapi()
    return {
        "route_count": len(app.routes),
        "openapi_path_count": len(schema.get("paths", {})),
    }


def _database_baseline() -> dict[str, Any]:
    from sqlalchemy import create_engine, inspect

    from app.database.bootstrap import bootstrap_database

    with tempfile.TemporaryDirectory(prefix="siming-performance-") as temp_dir:
        database = Path(temp_dir) / "fresh.db"
        url = f"sqlite:///{database.as_posix()}"
        engine = create_engine(url)
        try:
            result = bootstrap_database(engine, database_url=url)
            if result.read_only:
                raise RuntimeError(result.message)
            return {
                "mode": result.mode,
                "schema_revision": result.schema_revision,
                "table_count": len(inspect(engine).get_table_names()),
            }
        finally:
            engine.dispose()


def collect_performance_baseline() -> dict[str, Any]:
    measurements: dict[str, dict[str, Any]] = {}
    total_started = time.perf_counter()
    for name, action in (
        ("tool_and_prompt_catalog", _catalog_baseline),
        ("app_factory_and_openapi", _application_baseline),
        ("fresh_database_bootstrap", _database_baseline),
    ):
        elapsed_ms, details = _measure(action)
        measurements[name] = {"elapsed_ms": elapsed_ms, **details}
    total_ms = round((time.perf_counter() - total_started) * 1000, 3)
    return {
        "schema_version": 1,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "budgets_ms": BUDGETS_MS,
        "measurements": measurements,
        "total_ms": total_ms,
    }


def _violations(report: dict[str, Any]) -> list[str]:
    violations = []
    for name, details in report["measurements"].items():
        elapsed = float(details["elapsed_ms"])
        budget = BUDGETS_MS[name]
        if elapsed > budget:
            violations.append(f"{name}: {elapsed:.1f} ms exceeds {budget:.1f} ms")
    if float(report["total_ms"]) > BUDGETS_MS["total"]:
        violations.append(
            f"total: {report['total_ms']:.1f} ms exceeds {BUDGETS_MS['total']:.1f} ms"
        )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build" / "performance.json"
    )
    parser.add_argument("--no-enforce", action="store_true")
    args = parser.parse_args()

    report = collect_performance_baseline()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    violations = [] if args.no_enforce else _violations(report)
    for violation in violations:
        print(f"PERF-ERROR: {violation}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
