#!/usr/bin/env python3
"""Rehearse a Siming database migration on a disposable SQLite copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CORE_TABLES = (
    "projects",
    "chapters",
    "characters",
    "outline_nodes",
    "worldbuilding_entries",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source_db,
        closing(sqlite3.connect(target)) as target_db,
    ):
        source_db.backup(target_db)


def _database_snapshot(path: Path) -> dict[str, Any]:
    uri = f"file:{path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in CORE_TABLES
            if table in tables
        }
    return {
        "integrity": integrity,
        "table_count": len(tables),
        "core_row_counts": counts,
    }


def rehearse_database_migration(source: Path, working_copy: Path) -> dict[str, Any]:
    from sqlalchemy import create_engine

    from app.database.bootstrap import bootstrap_database

    source = source.expanduser().resolve()
    working_copy = working_copy.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    source_hash_before = _sha256(source)
    _copy_database(source, working_copy)
    before = _database_snapshot(working_copy)
    url = f"sqlite:///{working_copy.as_posix()}"
    engine = create_engine(url)
    try:
        result = bootstrap_database(engine, database_url=url)
    finally:
        engine.dispose()
    after = _database_snapshot(working_copy)
    source_hash_after = _sha256(source)
    preserved = all(
        after["core_row_counts"].get(table, 0) >= count
        for table, count in before["core_row_counts"].items()
    )
    success = (
        not result.read_only
        and before["integrity"] == "ok"
        and after["integrity"] == "ok"
        and source_hash_before == source_hash_after
        and preserved
    )
    return {
        "schema_version": 1,
        "success": success,
        "source": str(source),
        "working_copy": str(working_copy),
        "source_unchanged": source_hash_before == source_hash_after,
        "source_sha256": source_hash_before,
        "migration": {
            "mode": result.mode,
            "read_only": result.read_only,
            "schema_revision": result.schema_revision,
            "message": result.message,
        },
        "before": before,
        "after": after,
        "core_rows_preserved": preserved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--working-copy", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.working_copy:
        working_copy = args.working_copy
        report = rehearse_database_migration(args.database, working_copy)
    else:
        with tempfile.TemporaryDirectory(
            prefix="siming-migration-rehearsal-"
        ) as temp_dir:
            report = rehearse_database_migration(
                args.database, Path(temp_dir) / "rehearsal.db"
            )
            report["working_copy"] = "discarded"
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
