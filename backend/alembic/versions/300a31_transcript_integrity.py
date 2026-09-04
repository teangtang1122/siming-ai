"""Quarantine invalid transcript ownership rows before enforcing SQLite FKs.

Revision ID: 300a31_transcript_integrity
Revises: 300a30_direct_mcp_integrity
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "300a31_transcript_integrity"
down_revision = "300a30_direct_mcp_integrity"
branch_labels = None
depends_on = None

_REPLICA_TABLE = "assistant_conversation_replicas"
_RECEIPT_TABLE = "assistant_transcript_import_receipts"
_QUARANTINE_TABLE = "data_integrity_quarantine"
_BATCH_TABLE = "data_integrity_quarantine_batches"
_QUARANTINE_COLUMNS = {
    "id",
    "migration_revision",
    "source_table",
    "source_id",
    "reason",
    "payload_json",
    "quarantined_at",
}
_BATCH_COLUMNS = {
    "migration_revision",
    "quarantined_receipt_count",
    "quarantined_replica_count",
    "completed_at",
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _require_exact_columns(table: str, expected: set[str]) -> None:
    columns = sa.inspect(op.get_bind()).get_columns(table)
    actual = {str(column["name"]) for column in columns}
    if actual != expected:
        raise RuntimeError(
            f"Migration drift: {table} columns must be exactly {sorted(expected)}, "
            f"found {sorted(actual)}"
        )
    nullable = sorted(
        str(column["name"]) for column in columns if bool(column.get("nullable"))
    )
    if nullable:
        raise RuntimeError(
            f"Migration drift: {table} audit columns must be NOT NULL, "
            f"found nullable {nullable}"
        )


def _require_primary_key(table: str, expected: list[str]) -> None:
    primary_key = sa.inspect(op.get_bind()).get_pk_constraint(table)
    actual = [str(column) for column in primary_key.get("constrained_columns") or []]
    if actual != expected:
        raise RuntimeError(
            f"Migration drift: {table} primary key must be {tuple(expected)}, "
            f"found {tuple(actual)}"
        )


def _require_unique_constraint(table: str, name: str, expected: list[str]) -> None:
    constraints = {
        str(constraint["name"]): [
            str(column) for column in constraint.get("column_names") or []
        ]
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table)
        if constraint.get("name")
    }
    if constraints.get(name) != expected:
        raise RuntimeError(
            f"Migration drift: {name} must be UNIQUE on {table}{tuple(expected)}"
        )


def _require_index(table: str, name: str, expected: list[str]) -> None:
    indexes = {
        str(index["name"]): dict(index)
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }
    existing = indexes.get(name)
    actual = [str(column) for column in (existing or {}).get("column_names") or []]
    if existing is None or bool(existing.get("unique")) or actual != expected:
        raise RuntimeError(
            f"Migration drift: {name} must index {table}{tuple(expected)}"
        )


def _validate_quarantine_tables() -> None:
    _require_exact_columns(_QUARANTINE_TABLE, _QUARANTINE_COLUMNS)
    _require_primary_key(_QUARANTINE_TABLE, ["id"])
    _require_unique_constraint(
        _QUARANTINE_TABLE,
        "uq_data_integrity_quarantine_source",
        ["migration_revision", "source_table", "source_id"],
    )
    _require_index(
        _QUARANTINE_TABLE,
        "ix_data_integrity_quarantine_migration_revision",
        ["migration_revision"],
    )
    _require_index(
        _QUARANTINE_TABLE,
        "ix_data_integrity_quarantine_source_table",
        ["source_table"],
    )
    _require_exact_columns(_BATCH_TABLE, _BATCH_COLUMNS)
    _require_primary_key(_BATCH_TABLE, ["migration_revision"])


def _create_quarantine_tables() -> None:
    tables = _tables()
    if _QUARANTINE_TABLE not in tables:
        op.create_table(
            _QUARANTINE_TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("migration_revision", sa.String(length=64), nullable=False),
            sa.Column("source_table", sa.String(length=100), nullable=False),
            sa.Column("source_id", sa.String(length=128), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("quarantined_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "migration_revision",
                "source_table",
                "source_id",
                name="uq_data_integrity_quarantine_source",
            ),
        )
        op.create_index(
            "ix_data_integrity_quarantine_migration_revision",
            _QUARANTINE_TABLE,
            ["migration_revision"],
        )
        op.create_index(
            "ix_data_integrity_quarantine_source_table",
            _QUARANTINE_TABLE,
            ["source_table"],
        )
    if _BATCH_TABLE not in tables:
        op.create_table(
            _BATCH_TABLE,
            sa.Column("migration_revision", sa.String(length=64), nullable=False),
            sa.Column("quarantined_receipt_count", sa.Integer(), nullable=False),
            sa.Column("quarantined_replica_count", sa.Integer(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("migration_revision"),
        )
    _validate_quarantine_tables()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "hex", "value": bytes(value).hex()}
    raise RuntimeError(f"Cannot preserve quarantine value of type {type(value).__name__}")


def _payload(row: sa.RowMapping) -> str:
    return json.dumps(
        {str(key): _json_value(value) for key, value in row.items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _all_rows(table: str) -> list[sa.RowMapping]:
    if table not in _tables():
        return []
    return list(op.get_bind().execute(sa.text(f'SELECT * FROM "{table}"')).mappings())


def _quarantine_id(source_table: str, source_id: str) -> str:
    value = f"{revision}\0{source_table}\0{source_id}".encode()
    return hashlib.sha256(value).hexdigest()


def _quarantine(row: sa.RowMapping, *, source_table: str, reasons: list[str]) -> None:
    source_id = str(row["id"])
    reason = "ownership_join_failed:" + ",".join(sorted(reasons))
    payload_json = _payload(row)
    connection = op.get_bind()
    existing = connection.execute(
        sa.text(
            f'SELECT reason, payload_json FROM "{_QUARANTINE_TABLE}" '
            "WHERE migration_revision = :migration_revision "
            "AND source_table = :source_table AND source_id = :source_id"
        ),
        {
            "migration_revision": revision,
            "source_table": source_table,
            "source_id": source_id,
        },
    ).mappings().one_or_none()
    if existing is not None:
        if existing["reason"] != reason or existing["payload_json"] != payload_json:
            raise RuntimeError(
                f"Integrity quarantine drift for {source_table} row {source_id}"
            )
        return
    connection.execute(
        sa.text(
            f'INSERT INTO "{_QUARANTINE_TABLE}" '
            "(id, migration_revision, source_table, source_id, reason, payload_json, "
            "quarantined_at) VALUES (:id, :migration_revision, :source_table, :source_id, "
            ":reason, :payload_json, :quarantined_at)"
        ),
        {
            "id": _quarantine_id(source_table, source_id),
            "migration_revision": revision,
            "source_table": source_table,
            "source_id": source_id,
            "reason": reason,
            "payload_json": payload_json,
            "quarantined_at": datetime.utcnow(),
        },
    )


def _invalid_replicas(
    *,
    project_ids: set[Any],
    conversation_projects: dict[Any, Any],
    replicas: list[sa.RowMapping],
) -> list[tuple[sa.RowMapping, list[str]]]:
    invalid: list[tuple[sa.RowMapping, list[str]]] = []
    for row in replicas:
        reasons: list[str] = []
        project_id = row["project_id"]
        conversation_id = row["assistant_conversation_id"]
        if project_id not in project_ids:
            reasons.append("missing_project")
        conversation_project = conversation_projects.get(conversation_id)
        if conversation_project is None:
            reasons.append("missing_conversation")
        elif conversation_project != project_id:
            reasons.append("conversation_project_mismatch")
        if reasons:
            invalid.append((row, reasons))
    return invalid


def _invalid_receipts(
    *,
    project_ids: set[Any],
    conversation_projects: dict[Any, Any],
    replicas_by_id: dict[Any, sa.RowMapping],
    invalid_replica_ids: set[Any],
    receipts: list[sa.RowMapping],
) -> list[tuple[sa.RowMapping, list[str]]]:
    invalid: list[tuple[sa.RowMapping, list[str]]] = []
    for row in receipts:
        reasons: list[str] = []
        project_id = row["project_id"]
        conversation_id = row["assistant_conversation_id"]
        if project_id not in project_ids:
            reasons.append("missing_project")
        conversation_project = conversation_projects.get(conversation_id)
        if conversation_project is None:
            reasons.append("missing_conversation")
        elif conversation_project != project_id:
            reasons.append("conversation_project_mismatch")
        replica = replicas_by_id.get(row["replica_id"])
        if replica is None:
            reasons.append("missing_replica")
        else:
            if replica["id"] in invalid_replica_ids:
                reasons.append("replica_owner_invalid")
            if replica["project_id"] != project_id:
                reasons.append("replica_project_mismatch")
            if replica["assistant_conversation_id"] != conversation_id:
                reasons.append("replica_conversation_mismatch")
            if replica["device_scope"] != row["device_scope"]:
                reasons.append("replica_device_mismatch")
        if reasons:
            invalid.append((row, reasons))
    return invalid


def _delete_rows(table: str, rows: list[tuple[sa.RowMapping, list[str]]]) -> None:
    if not rows:
        return
    op.get_bind().execute(
        sa.text(f'DELETE FROM "{table}" WHERE id = :id'),
        [{"id": row["id"]} for row, _reasons in rows],
    )
    deleted_ids = {row["id"] for row, _reasons in rows}
    remaining_ids = {row["id"] for row in _all_rows(table)}
    if deleted_ids & remaining_ids:
        raise RuntimeError(f"Integrity quarantine could not purge all invalid {table} rows")


def _quarantine_count(source_table: str) -> int:
    value = op.get_bind().execute(
        sa.text(
            f'SELECT COUNT(*) FROM "{_QUARANTINE_TABLE}" '
            "WHERE migration_revision = :migration_revision AND source_table = :source_table"
        ),
        {"migration_revision": revision, "source_table": source_table},
    ).scalar_one()
    return int(value or 0)


def _record_batch(*, receipt_count: int, replica_count: int) -> None:
    connection = op.get_bind()
    existing = connection.execute(
        sa.text(
            f'SELECT quarantined_receipt_count, quarantined_replica_count FROM "{_BATCH_TABLE}" '
            "WHERE migration_revision = :migration_revision"
        ),
        {"migration_revision": revision},
    ).mappings().one_or_none()
    expected = (receipt_count, replica_count)
    if existing is not None:
        actual = (
            int(existing["quarantined_receipt_count"]),
            int(existing["quarantined_replica_count"]),
        )
        if actual != expected:
            raise RuntimeError(
                f"Integrity quarantine count drift: recorded {actual}, found {expected}"
            )
        return
    connection.execute(
        sa.text(
            f'INSERT INTO "{_BATCH_TABLE}" '
            "(migration_revision, quarantined_receipt_count, quarantined_replica_count, "
            "completed_at) VALUES (:migration_revision, :receipt_count, :replica_count, "
            ":completed_at)"
        ),
        {
            "migration_revision": revision,
            "receipt_count": receipt_count,
            "replica_count": replica_count,
            "completed_at": datetime.utcnow(),
        },
    )


def _verify_sqlite_integrity() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    tables = _tables()
    for table in (_RECEIPT_TABLE, _REPLICA_TABLE):
        if table not in tables:
            continue
        violations = list(
            connection.exec_driver_sql(f'PRAGMA foreign_key_check("{table}")').fetchall()
        )
        if violations:
            raise RuntimeError(f"Foreign-key violations remain in {table}: {violations!r}")


def upgrade() -> None:
    _create_quarantine_tables()
    project_ids = {row["id"] for row in _all_rows("projects")}
    conversation_projects = {
        row["id"]: row["project_id"] for row in _all_rows("assistant_conversations")
    }
    replicas = _all_rows(_REPLICA_TABLE)
    receipts = _all_rows(_RECEIPT_TABLE)
    replicas_by_id = {row["id"]: row for row in replicas}
    invalid_replicas = _invalid_replicas(
        project_ids=project_ids,
        conversation_projects=conversation_projects,
        replicas=replicas,
    )
    invalid_replica_ids = {row["id"] for row, _reasons in invalid_replicas}
    invalid_receipts = _invalid_receipts(
        project_ids=project_ids,
        conversation_projects=conversation_projects,
        replicas_by_id=replicas_by_id,
        invalid_replica_ids=invalid_replica_ids,
        receipts=receipts,
    )

    for row, reasons in invalid_receipts:
        _quarantine(row, source_table=_RECEIPT_TABLE, reasons=reasons)
    for row, reasons in invalid_replicas:
        _quarantine(row, source_table=_REPLICA_TABLE, reasons=reasons)
    _delete_rows(_RECEIPT_TABLE, invalid_receipts)
    _delete_rows(_REPLICA_TABLE, invalid_replicas)

    _record_batch(
        receipt_count=_quarantine_count(_RECEIPT_TABLE),
        replica_count=_quarantine_count(_REPLICA_TABLE),
    )
    _verify_sqlite_integrity()


def downgrade() -> None:
    # Quarantine is an audit record, not application state. Retain it across a
    # schema downgrade so removed source payloads are never silently destroyed.
    pass
