"""Migration-specific ordering tests for conversation sequence backfill."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "300a28_conversation_context.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_300a28_conversation_context",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "table_name",
    ["assistant_messages", "system_assistant_messages"],
)
def test_sqlite_sequence_backfill_preserves_inserted_turn_pairs(
    monkeypatch: pytest.MonkeyPatch,
    table_name: str,
) -> None:
    migration = _load_migration()
    engine = create_engine("sqlite:///:memory:")
    same_timestamp = "2026-08-29 12:00:00"
    inserted = [
        ("z-user-1", "conversation-1", "user"),
        ("a-assistant-1", "conversation-1", "assistant"),
        ("y-user-2", "conversation-1", "user"),
        ("b-assistant-2", "conversation-1", "assistant"),
    ]
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f'CREATE TABLE "{table_name}" ('
                    "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "role TEXT NOT NULL, created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL, sequence_no INTEGER)"
                )
            )
            for message_id, conversation_id, role in inserted:
                connection.execute(
                    text(
                        f'INSERT INTO "{table_name}" '
                        "(id, conversation_id, role, created_at, updated_at) "
                        "VALUES (:id, :conversation_id, :role, :created_at, :updated_at)"
                    ),
                    {
                        "id": message_id,
                        "conversation_id": conversation_id,
                        "role": role,
                        "created_at": same_timestamp,
                        "updated_at": same_timestamp,
                    },
                )
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            migration._backfill_sequence(table_name)
            rows = connection.execute(
                text(f'SELECT id, sequence_no FROM "{table_name}" ORDER BY sequence_no ASC')
            ).all()
    finally:
        engine.dispose()

    assert [(row.id, row.sequence_no) for row in rows] == [
        (message_id, index)
        for index, (message_id, _conversation_id, _role) in enumerate(inserted, 1)
    ]


def test_non_sqlite_sequence_fallback_is_stable_and_role_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    statements: list[str] = []

    class _Bind:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement: Any, parameters: Any = None) -> list[Any]:
            statements.append(str(statement))
            return []

    monkeypatch.setattr(migration.op, "get_bind", _Bind)
    migration._backfill_sequence("assistant_messages")

    assert statements == [
        'SELECT id, conversation_id FROM "assistant_messages" '
        "ORDER BY conversation_id ASC, created_at ASC, updated_at ASC, id ASC"
    ]
    assert "role" not in statements[0]


def test_partial_migration_keeps_complete_existing_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE assistant_messages ("
                    "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "sequence_no INTEGER)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assistant_messages "
                    "(id, conversation_id, sequence_no) VALUES "
                    "('z-user', 'conversation', 1), "
                    "('a-assistant', 'conversation', 2)"
                )
            )
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

            assert migration._sequence_backfill_required("assistant_messages") is False
            rows = connection.execute(
                text("SELECT id, sequence_no FROM assistant_messages ORDER BY sequence_no")
            ).all()
    finally:
        engine.dispose()

    assert [(row.id, row.sequence_no) for row in rows] == [
        ("z-user", 1),
        ("a-assistant", 2),
    ]


def test_partial_migration_refuses_mixed_or_invalid_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE assistant_messages ("
                    "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "sequence_no INTEGER)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assistant_messages "
                    "(id, conversation_id, sequence_no) VALUES "
                    "('user', 'conversation', 1), "
                    "('assistant', 'conversation', NULL)"
                )
            )
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

            with pytest.raises(RuntimeError, match="partially populated"):
                migration._sequence_backfill_required("assistant_messages")
            connection.execute(
                text(
                    "UPDATE assistant_messages SET sequence_no = 3 "
                    "WHERE id = 'assistant'"
                )
            )
            with pytest.raises(RuntimeError, match="is invalid"):
                migration._sequence_backfill_required("assistant_messages")
    finally:
        engine.dispose()
