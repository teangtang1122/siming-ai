"""SQLite online-backup and recovery guarantees."""

import sqlite3
from contextlib import closing
from pathlib import Path

from app.database.backup import backup_sqlite_database


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_backup_includes_committed_wal_rows(tmp_path: Path) -> None:
    source = tmp_path / "story.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE chapters (id TEXT PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO chapters VALUES ('c1', 'still in wal')")
        connection.commit()

        backup = backup_sqlite_database(_url(source), reason="wal-test")

        assert backup is not None
        with closing(sqlite3.connect(backup)) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.execute("SELECT content FROM chapters").fetchone()[0] == "still in wal"
    finally:
        connection.close()


def test_consecutive_backups_do_not_overwrite_each_other(tmp_path: Path) -> None:
    source = tmp_path / "story.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        connection.commit()

    first = backup_sqlite_database(_url(source), reason="repeat")
    second = backup_sqlite_database(_url(source), reason="repeat")

    assert first is not None and second is not None
    assert first != second
    assert first.is_file() and second.is_file()
