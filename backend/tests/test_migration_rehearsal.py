"""End-to-end migration rehearsal against a representative legacy database."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def test_rehearsal_preserves_richer_legacy_story_data(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    working = tmp_path / "rehearsed.db"
    report_path = tmp_path / "report.json"
    with closing(sqlite3.connect(source)) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT);
            CREATE TABLE chapters (id TEXT PRIMARY KEY, project_id TEXT, title TEXT, content TEXT);
            CREATE TABLE characters (id TEXT PRIMARY KEY, project_id TEXT, name TEXT);
            CREATE TABLE outline_nodes (id TEXT PRIMARY KEY, project_id TEXT, title TEXT);
            CREATE TABLE worldbuilding_entries (
                id TEXT PRIMARY KEY, project_id TEXT, title TEXT, content TEXT
            );
            INSERT INTO projects VALUES ('p1', 'Long Story');
            INSERT INTO chapters VALUES ('c1', 'p1', 'Chapter 1', 'body');
            INSERT INTO characters VALUES ('r1', 'p1', 'Hero');
            INSERT INTO outline_nodes VALUES ('o1', 'p1', 'Opening');
            INSERT INTO worldbuilding_entries VALUES ('w1', 'p1', 'Capital', 'details');
            """
        )
        connection.commit()

    source_before = source.read_bytes()
    script = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_database_migration.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(source),
            "--working-copy",
            str(working),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["source_unchanged"] is True
    assert report["core_rows_preserved"] is True
    assert report["migration"]["schema_revision"] == "300a2_content_sync"
    assert source.read_bytes() == source_before
    assert report["after"]["core_row_counts"] == report["before"]["core_row_counts"]
