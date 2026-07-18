"""SQLite backup helpers used before runtime schema migrations."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


def sqlite_database_path(database_url: str) -> Path | None:
    """Return a filesystem path for file-backed SQLite URLs."""
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def backup_sqlite_database(database_url: str, *, reason: str = "startup") -> Path | None:
    """Create a consistent SQLite backup, including committed WAL contents.

    SQLite's online backup API is used instead of copying the main file because
    recently committed data may still live in the write-ahead log.
    """
    db_path = sqlite_database_path(database_url)
    if not db_path or not db_path.exists() or db_path.stat().st_size <= 0:
        return None

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    clean_reason = "".join(ch for ch in reason if ch.isalnum() or ch in {"-", "_"})[:40] or "backup"
    backup_path = backup_dir / f"{db_path.stem}.{clean_reason}.{timestamp}{db_path.suffix}"
    temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.tmp")
    source_uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source,
            closing(sqlite3.connect(temporary_path)) as destination,
        ):
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise sqlite3.DatabaseError("SQLite backup failed its integrity check")
        temporary_path.replace(backup_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path
