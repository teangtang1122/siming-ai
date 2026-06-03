"""SQLite backup helpers used before runtime schema migrations."""
from __future__ import annotations

import shutil
from datetime import datetime
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
    """Copy the SQLite database to a timestamped backup file if it exists.

    The backup is intentionally conservative and runs before runtime schema sync.
    This protects users who open a newer exe with data created by an older exe.
    """
    db_path = sqlite_database_path(database_url)
    if not db_path or not db_path.exists() or db_path.stat().st_size <= 0:
        return None

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    clean_reason = "".join(ch for ch in reason if ch.isalnum() or ch in {"-", "_"})[:40] or "backup"
    backup_path = backup_dir / f"{db_path.stem}.{clean_reason}.{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path
