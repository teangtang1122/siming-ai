"""SQLite migration contract for durable Direct-MCP identities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.database.bootstrap import alembic_config

_HEAD = "300a30_direct_mcp_integrity"
_PARENT = "300a29_transcript_imports"


def _column_names(engine, table: str) -> set[str]:
    return {str(column["name"]) for column in inspect(engine).get_columns(table)}


def _has_unique_identity(engine, table: str, columns: Iterable[str]) -> bool:
    expected = list(columns)
    inspector = inspect(engine)
    return any(
        bool(index.get("unique")) and list(index.get("column_names") or []) == expected
        for index in inspector.get_indexes(table)
    ) or any(
        list(constraint.get("column_names") or []) == expected
        for constraint in inspector.get_unique_constraints(table)
    )


def _insert_run(engine, *, run_id: str, lease_hash: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assistant_runs ("
                "id, project_id, status, current_iteration, direct_mcp_lease_hash, "
                "created_at, updated_at"
                ") VALUES ("
                ":id, :project_id, 'running', 1, :lease_hash, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "id": run_id,
                "project_id": "migration-project",
                "lease_hash": lease_hash,
            },
        )


def _insert_step(engine, *, step_id: str, run_id: str, call_key: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assistant_run_steps ("
                "id, run_id, project_id, step_type, status, iteration, attempt_no, "
                "direct_mcp_call_key, created_at, updated_at"
                ") VALUES ("
                ":id, :run_id, :project_id, 'write', 'running', 1, 1, "
                ":call_key, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "id": step_id,
                "run_id": run_id,
                "project_id": "migration-project",
                "call_key": call_key,
            },
        )


def _assert_head_schema(engine) -> None:
    assert {
        "direct_mcp_lease_hash",
        "direct_mcp_lease_iteration",
    } <= _column_names(engine, "assistant_runs")
    assert "direct_mcp_call_key" in _column_names(engine, "assistant_run_steps")
    assert _has_unique_identity(
        engine,
        "assistant_runs",
        ["direct_mcp_lease_hash"],
    )
    assert _has_unique_identity(
        engine,
        "assistant_run_steps",
        ["direct_mcp_call_key"],
    )


def test_300a29_sqlite_round_trip_and_unique_identities(tmp_path: Path) -> None:
    database_path = tmp_path / "direct-mcp-migration.db"
    config = alembic_config(f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, _HEAD)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _assert_head_schema(engine)
    engine.dispose()
    command.downgrade(config, _PARENT)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    assert "direct_mcp_lease_hash" not in _column_names(engine, "assistant_runs")
    assert "direct_mcp_lease_iteration" not in _column_names(engine, "assistant_runs")
    assert "direct_mcp_call_key" not in _column_names(engine, "assistant_run_steps")
    engine.dispose()

    command.upgrade(config, _HEAD)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    _assert_head_schema(engine)

    _insert_run(engine, run_id="run-1", lease_hash="a" * 64)
    with pytest.raises(IntegrityError):
        _insert_run(engine, run_id="run-duplicate-lease", lease_hash="a" * 64)
    _insert_run(engine, run_id="run-2", lease_hash="b" * 64)
    _insert_run(engine, run_id="run-null-lease-1", lease_hash=None)
    _insert_run(engine, run_id="run-null-lease-2", lease_hash=None)

    _insert_step(engine, step_id="step-1", run_id="run-1", call_key="call-1")
    with pytest.raises(IntegrityError):
        _insert_step(engine, step_id="step-duplicate", run_id="run-2", call_key="call-1")
    _insert_step(engine, step_id="step-null-1", run_id="run-1", call_key=None)
    _insert_step(engine, step_id="step-null-2", run_id="run-2", call_key=None)
    engine.dispose()


@pytest.mark.parametrize(
    ("table", "index_name"),
    [
        ("assistant_runs", "uq_assistant_runs_direct_mcp_lease_hash"),
        ("assistant_run_steps", "uq_run_steps_direct_mcp_call_key"),
    ],
)
def test_300a29_rejects_same_named_non_unique_or_wrong_column_index(
    tmp_path: Path,
    table: str,
    index_name: str,
) -> None:
    database_path = tmp_path / f"drift-{table}.db"
    config = alembic_config(f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, _HEAD)
    command.downgrade(config, _PARENT)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table} (status)"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="Migration drift"):
        command.upgrade(config, _HEAD)
