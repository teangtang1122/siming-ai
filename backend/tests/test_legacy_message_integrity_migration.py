"""Regression coverage for the SQLite conversation migration recovery."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database.bootstrap import alembic_config, bootstrap_database
from app.database.session import create_session_engine

_HEAD = "300a34_canonical_model_identity"
_INTEGRITY_REVISION = "300a33_legacy_message_integrity"
_PRE_SEQUENCE_REVISION = "300a27_chapter_revision_drafts"
_PRE_REPAIR_REVISION = "300a32_context_source_ids"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _initialize_at_revision(path: Path, revision: str) -> str:
    url = _database_url(path)
    engine = create_session_engine(url)
    try:
        initialized = bootstrap_database(engine, database_url=url)
        assert initialized.schema_revision == _HEAD
        config = alembic_config(url)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, revision)
    finally:
        engine.dispose()
    return url


def _insert_legacy_transcripts(
    path: Path,
    *,
    simulate_partial_sequence: bool = True,
) -> None:
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        connection.execute(
            "INSERT INTO projects (id, title, created_at, updated_at) "
            "VALUES ('p1', 'Legacy project', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO assistant_conversations "
            "(id, project_id, title, scope, created_at, updated_at) "
            "VALUES ('c1', 'p1', 'Workspace', 'writer', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.executemany(
            "INSERT INTO assistant_messages "
            "(id, conversation_id, role, content, payload_json, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'completed', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [
                ("u1", "c1", "user", "question", None),
                (
                    "a1",
                    "c1",
                    "assistant",
                    "answer",
                    json.dumps({"run": {"id": "r1"}}),
                ),
                ("orphan-workspace", "missing-workspace", "user", "keep me", None),
            ],
        )
        connection.execute(
            "INSERT INTO assistant_runs "
            "(id, project_id, conversation_id, user_message_id, "
            "assistant_message_id, status, current_iteration, created_at, updated_at) "
            "VALUES ('r1', 'p1', 'c1', 'u1', 'a1', 'completed', 1, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO system_assistant_conversations "
            "(id, title, scope_type, created_at, updated_at) "
            "VALUES ('sc1', 'Creation', 'global', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.executemany(
            "INSERT INTO system_assistant_messages "
            "(id, conversation_id, role, content, operation_id, message_type, status, "
            "created_at, updated_at) VALUES (?, ?, 'assistant', ?, ?, 'text', "
            "'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [
                ("system-missing-operation", "sc1", "repair me", "missing-operation"),
                ("orphan-system", "missing-system", "preserve me", None),
            ],
        )
        if simulate_partial_sequence:
            # Reproduce the state left behind when 3.3.10 failed after
            # backfilling sequence_no but before Alembic stamped 300a28.
            connection.execute(
                "ALTER TABLE assistant_messages ADD COLUMN sequence_no INTEGER"
            )
            connection.executemany(
                "UPDATE assistant_messages SET sequence_no = ? WHERE id = ?",
                [(1, "u1"), (2, "a1"), (1, "orphan-workspace")],
            )
        connection.commit()


def test_retries_the_real_foreign_key_failure_from_the_shipped_migration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "failed-300a28.db"
    url = _initialize_at_revision(database_path, _PRE_SEQUENCE_REVISION)
    _insert_legacy_transcripts(database_path, simulate_partial_sequence=False)
    engine = create_session_engine(url)
    try:
        config = alembic_config(url)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            config.attributes["connection"] = connection
            command.upgrade(config, "300a28_conversation_context")

        recovered = bootstrap_database(engine, database_url=url)

        assert recovered.mode == "migrated"
        assert recovered.read_only is False
        assert recovered.schema_revision == _HEAD
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            run = connection.execute(
                text(
                    "SELECT user_message_id, assistant_message_id "
                    "FROM assistant_runs WHERE id = 'r1'"
                )
            ).one()
        assert tuple(run) == ("u1", "a1")
    finally:
        engine.dispose()


def test_partial_300a28_failure_recovers_without_clearing_valid_run_refs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-300a28.db"
    url = _initialize_at_revision(database_path, _PRE_SEQUENCE_REVISION)
    _insert_legacy_transcripts(database_path)

    engine = create_session_engine(url)
    try:
        recovered = bootstrap_database(engine, database_url=url)

        assert recovered.mode == "migrated"
        assert recovered.read_only is False
        assert recovered.schema_revision == _HEAD
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            run = connection.execute(
                text(
                    "SELECT user_message_id, assistant_message_id "
                    "FROM assistant_runs WHERE id = 'r1'"
                )
            ).one()
            assert tuple(run) == ("u1", "a1")
            assert connection.execute(
                text(
                    "SELECT id FROM assistant_messages "
                    "WHERE id = 'orphan-workspace'"
                )
            ).one_or_none() is None
            system_row = connection.execute(
                text(
                    "SELECT operation_id FROM system_assistant_messages "
                    "WHERE id = 'system-missing-operation'"
                )
            ).one()
            assert system_row.operation_id is None
            assert connection.execute(
                text(
                    "SELECT id FROM system_assistant_messages "
                    "WHERE id = 'orphan-system'"
                )
            ).one_or_none() is None
            quarantine = connection.execute(
                text(
                    "SELECT source_table, source_id, reason, payload_json "
                    "FROM data_integrity_quarantine "
                    "WHERE migration_revision = :revision ORDER BY source_id"
                ),
                {"revision": _INTEGRITY_REVISION},
            ).mappings().all()
        assert [(row["source_table"], row["source_id"], row["reason"]) for row in quarantine] == [
            (
                "system_assistant_messages",
                "orphan-system",
                "legacy_integrity_repair:missing_conversation",
            ),
            (
                "assistant_messages",
                "orphan-workspace",
                "legacy_integrity_repair:missing_conversation",
            ),
            (
                "system_assistant_messages",
                "system-missing-operation",
                "legacy_integrity_repair:missing_operation",
            ),
        ]
        payloads = {
            row["source_id"]: json.loads(row["payload_json"]) for row in quarantine
        }
        assert payloads["orphan-workspace"]["content"] == "keep me"
        assert payloads["orphan-system"]["content"] == "preserve me"
    finally:
        engine.dispose()


def test_already_migrated_database_repairs_only_deterministic_run_refs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cleared-run-refs.db"
    url = _initialize_at_revision(database_path, _PRE_REPAIR_REVISION)
    engine = create_session_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, title, created_at, updated_at) "
                    "VALUES ('p1', 'Migrated project', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assistant_conversations "
                    "(id, project_id, title, scope, created_at, updated_at) "
                    "VALUES ('c1', 'p1', 'Workspace', 'writer', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assistant_messages "
                    "(id, conversation_id, role, sequence_no, content, payload_json, status, "
                    "created_at, updated_at) VALUES "
                    "('u1', 'c1', 'user', 1, 'question', NULL, 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('a1', 'c1', 'assistant', 2, 'answer', :run_one, 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('u2', 'c1', 'user', 3, 'question 2', NULL, 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('a2', 'c1', 'assistant', 4, 'answer 2', :run_two, 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('a3', 'c1', 'assistant', 5, 'duplicate', :run_two, 'completed', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "run_one": json.dumps({"run": {"id": "r1"}}),
                    "run_two": json.dumps({"run": {"id": "r2"}}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO assistant_runs "
                    "(id, project_id, conversation_id, status, current_iteration, "
                    "created_at, updated_at) VALUES "
                    "('r1', 'p1', 'c1', 'completed', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('r2', 'p1', 'c1', 'completed', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        migrated = bootstrap_database(engine, database_url=url)

        assert migrated.schema_revision == _HEAD
        with engine.connect() as connection:
            refs = connection.execute(
                text(
                    "SELECT id, user_message_id, assistant_message_id "
                    "FROM assistant_runs ORDER BY id"
                )
            ).all()
            assert [tuple(row) for row in refs] == [
                ("r1", "u1", "a1"),
                ("r2", None, None),
            ]
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()
