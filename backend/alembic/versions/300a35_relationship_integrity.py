"""Make one directed character pair map to one current relationship.

Revision ID: 300a35_relationship_integrity
Revises: 300a34_canonical_model_identity
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "300a35_relationship_integrity"
down_revision = "300a34_canonical_model_identity"
branch_labels = None
depends_on = None

_RELATIONSHIPS = "character_relationships"
_QUARANTINE = "data_integrity_quarantine"
_CONSTRAINT = "uq_character_relationships_directed_pair"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "hex", "value": bytes(value).hex()}
    raise RuntimeError(f"Cannot preserve relationship value of type {type(value).__name__}")


def _payload(row: sa.RowMapping) -> str:
    return json.dumps(
        {str(key): _json_value(value) for key, value in row.items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _quarantine_id(source_id: str) -> str:
    return hashlib.sha256(f"{revision}\0{_RELATIONSHIPS}\0{source_id}".encode()).hexdigest()


def _quarantine_duplicate(row: sa.RowMapping, survivor_id: str) -> None:
    connection = op.get_bind()
    source_id = str(row["id"])
    connection.execute(
        sa.text(
            f'INSERT INTO "{_QUARANTINE}" '
            "(id, migration_revision, source_table, source_id, reason, payload_json, quarantined_at) "
            "VALUES (:id, :migration_revision, :source_table, :source_id, :reason, :payload_json, :quarantined_at)"
        ),
        {
            "id": _quarantine_id(source_id),
            "migration_revision": revision,
            "source_table": _RELATIONSHIPS,
            "source_id": source_id,
            "reason": f"duplicate_directed_relationship_pair:survivor={survivor_id}",
            "payload_json": _payload(row),
            "quarantined_at": datetime.utcnow(),
        },
    )


def _created_key(row: sa.RowMapping) -> tuple[datetime, str]:
    value = row.get("created_at")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            value = None
    return (value if isinstance(value, datetime) else datetime.min, str(row["id"]))


def _repair_duplicates() -> None:
    tables = _tables()
    if _RELATIONSHIPS not in tables:
        return
    if _QUARANTINE not in tables:
        raise RuntimeError("Relationship integrity repair requires data_integrity_quarantine")

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(f'SELECT * FROM "{_RELATIONSHIPS}"')
        ).mappings()
    )
    groups: dict[tuple[str, str, str], list[sa.RowMapping]] = {}
    for row in rows:
        key = (
            str(row["project_id"]),
            str(row["character_a_id"]),
            str(row["character_b_id"]),
        )
        groups.setdefault(key, []).append(row)

    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        # The newest row is the current user/model decision.  Older rows stay
        # available in the integrity quarantine instead of silently vanishing.
        survivor = max(duplicates, key=_created_key)
        for row in duplicates:
            if row["id"] == survivor["id"]:
                continue
            _quarantine_duplicate(row, str(survivor["id"]))
            connection.execute(
                sa.text(f'DELETE FROM "{_RELATIONSHIPS}" WHERE id = :id'),
                {"id": row["id"]},
            )


def upgrade() -> None:
    if _RELATIONSHIPS not in _tables():
        return
    _repair_duplicates()
    with op.batch_alter_table(_RELATIONSHIPS) as batch:
        batch.create_unique_constraint(
            _CONSTRAINT,
            ["project_id", "character_a_id", "character_b_id"],
        )


def downgrade() -> None:
    if _RELATIONSHIPS not in _tables():
        return
    with op.batch_alter_table(_RELATIONSHIPS) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="unique")
