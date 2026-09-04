"""Repair legacy assistant-message ownership and references.

Revision ID: 300a33_legacy_message_integrity
Revises: 300a32_context_source_ids
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "300a33_legacy_message_integrity"
down_revision = "300a32_context_source_ids"
branch_labels = None
depends_on = None

_QUARANTINE_TABLE = "data_integrity_quarantine"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _all_rows(table: str) -> list[sa.RowMapping]:
    if table not in _tables():
        return []
    return list(op.get_bind().execute(sa.text(f'SELECT * FROM "{table}"')).mappings())


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


def _quarantine_id(source_table: str, source_id: str) -> str:
    value = f"{revision}\0{source_table}\0{source_id}".encode()
    return hashlib.sha256(value).hexdigest()


def _quarantine(row: sa.RowMapping, *, source_table: str, reasons: list[str]) -> None:
    source_id = str(row["id"])
    reason = "legacy_integrity_repair:" + ",".join(sorted(reasons))
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


def _repair_orphan_workspace_messages() -> None:
    tables = _tables()
    if not {"assistant_messages", "assistant_conversations"} <= tables:
        return
    conversation_ids = {row["id"] for row in _all_rows("assistant_conversations")}
    invalid = [
        row
        for row in _all_rows("assistant_messages")
        if row["conversation_id"] not in conversation_ids
    ]
    for row in invalid:
        _quarantine(
            row,
            source_table="assistant_messages",
            reasons=["missing_conversation"],
        )
    if not invalid:
        return
    identifiers = [{"message_id": row["id"]} for row in invalid]
    connection = op.get_bind()
    if "assistant_runs" in tables:
        connection.execute(
            sa.text(
                "UPDATE assistant_runs SET user_message_id = NULL "
                "WHERE user_message_id = :message_id"
            ),
            identifiers,
        )
        connection.execute(
            sa.text(
                "UPDATE assistant_runs SET assistant_message_id = NULL "
                "WHERE assistant_message_id = :message_id"
            ),
            identifiers,
        )
    connection.execute(
        sa.text("DELETE FROM assistant_messages WHERE id = :message_id"),
        identifiers,
    )


def _repair_system_messages() -> None:
    tables = _tables()
    if not {"system_assistant_messages", "system_assistant_conversations"} <= tables:
        return
    conversation_ids = {row["id"] for row in _all_rows("system_assistant_conversations")}
    operation_ids = (
        {row["id"] for row in _all_rows("operation_runs")}
        if "operation_runs" in tables
        else set()
    )
    delete_ids: list[dict[str, Any]] = []
    clear_operation_ids: list[dict[str, Any]] = []
    for row in _all_rows("system_assistant_messages"):
        reasons: list[str] = []
        if row["conversation_id"] not in conversation_ids:
            reasons.append("missing_conversation")
        operation_id = row.get("operation_id")
        if operation_id is not None and operation_id not in operation_ids:
            reasons.append("missing_operation")
        if not reasons:
            continue
        _quarantine(row, source_table="system_assistant_messages", reasons=reasons)
        if "missing_conversation" in reasons:
            delete_ids.append({"message_id": row["id"]})
        else:
            clear_operation_ids.append({"message_id": row["id"]})
    connection = op.get_bind()
    if clear_operation_ids:
        connection.execute(
            sa.text(
                "UPDATE system_assistant_messages SET operation_id = NULL "
                "WHERE id = :message_id"
            ),
            clear_operation_ids,
        )
    if delete_ids:
        connection.execute(
            sa.text("DELETE FROM system_assistant_messages WHERE id = :message_id"),
            delete_ids,
        )


def _payload_run_id(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    run = value.get("run")
    if not isinstance(run, dict):
        return None
    run_id = str(run.get("id") or "").strip()
    return run_id or None


def _repair_workspace_run_references() -> None:
    tables = _tables()
    if not {"assistant_runs", "assistant_messages"} <= tables:
        return
    messages = _all_rows("assistant_messages")
    messages_by_id = {row["id"]: row for row in messages}
    messages_by_position = {
        (row["conversation_id"], int(row["sequence_no"])): row
        for row in messages
        if row.get("sequence_no") is not None
    }
    assistants_by_run: dict[str, list[sa.RowMapping]] = {}
    for row in messages:
        if str(row["role"]) != "assistant":
            continue
        run_id = _payload_run_id(row.get("payload_json"))
        if run_id:
            assistants_by_run.setdefault(run_id, []).append(row)

    updates: list[dict[str, Any]] = []
    for run in _all_rows("assistant_runs"):
        conversation_id = run.get("conversation_id")
        assistant = messages_by_id.get(run.get("assistant_message_id"))
        if assistant is None:
            candidates = [
                row
                for row in assistants_by_run.get(str(run["id"]), [])
                if conversation_id is None
                or row["conversation_id"] == conversation_id
            ]
            assistant = candidates[0] if len(candidates) == 1 else None

        user = messages_by_id.get(run.get("user_message_id"))
        if user is None:
            user = None
            if assistant is not None and assistant.get("sequence_no") is not None:
                candidate = messages_by_position.get(
                    (
                        assistant["conversation_id"],
                        int(assistant["sequence_no"]) - 1,
                    )
                )
                if candidate is not None and candidate["role"] == "user":
                    user = candidate

        assistant_id = assistant["id"] if assistant is not None else None
        user_id = user["id"] if user is not None else None
        if (
            run.get("assistant_message_id") != assistant_id
            or run.get("user_message_id") != user_id
        ):
            updates.append(
                {
                    "run_id": run["id"],
                    "assistant_message_id": assistant_id,
                    "user_message_id": user_id,
                }
            )
    if updates:
        op.get_bind().execute(
            sa.text(
                "UPDATE assistant_runs SET user_message_id = :user_message_id, "
                "assistant_message_id = :assistant_message_id WHERE id = :run_id"
            ),
            updates,
        )


def _verify_message_integrity() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        return
    for table in ("assistant_messages", "system_assistant_messages"):
        if table not in _tables():
            continue
        violations = list(
            connection.exec_driver_sql(f'PRAGMA foreign_key_check("{table}")').fetchall()
        )
        if violations:
            raise RuntimeError(f"Foreign-key violations remain in {table}: {violations!r}")


def upgrade() -> None:
    if _QUARANTINE_TABLE not in _tables():
        raise RuntimeError("Missing data-integrity quarantine table")
    _repair_orphan_workspace_messages()
    _repair_system_messages()
    _repair_workspace_run_references()
    _verify_message_integrity()


def downgrade() -> None:
    # Repairs restore declared FK semantics and quarantine removed payloads.
    # Reintroducing invalid references would corrupt the database again.
    pass
