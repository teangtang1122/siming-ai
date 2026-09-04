"""Widen durable checkpoint source IDs for canonical Creation turn IDs.

Revision ID: 300a32_context_source_ids
Revises: 300a31_transcript_integrity
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a32_context_source_ids"
down_revision = "300a31_transcript_integrity"
branch_labels = None
depends_on = None

_TABLE = "conversation_context_checkpoint_sources"
_COLUMN = "source_id"
_OLD_LENGTH = 36
_NEW_LENGTH = 128
_EXPECTED_COLUMNS = {
    "id",
    "checkpoint_id",
    "source_kind",
    "source_id",
    "source_sequence",
    "source_hash",
    "created_at",
}


def _source_id_length() -> int | None:
    inspector = sa.inspect(op.get_bind())
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError(f"Migration drift: missing {_TABLE}")
    columns = inspector.get_columns(_TABLE)
    actual_columns = {str(item["name"]) for item in columns}
    if actual_columns != _EXPECTED_COLUMNS:
        raise RuntimeError(f"Migration drift: {_TABLE} columns are {sorted(actual_columns)!r}")
    column = next((item for item in columns if item["name"] == _COLUMN), None)
    if column is None:
        raise RuntimeError(f"Migration drift: missing {_TABLE}.{_COLUMN}")
    return getattr(column["type"], "length", None)


def _sqlite_source_table(*, source_id_length: int) -> sa.Table:
    """Describe the known 300a28 table without reflecting its incomplete FK graph."""

    metadata = sa.MetaData()
    sa.Table(
        "conversation_context_checkpoints",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True),
    )
    table = sa.Table(
        _TABLE,
        metadata,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=source_id_length), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('message', 'run_step', 'prior_segment')",
            name="ck_context_checkpoint_source_kind",
        ),
        sa.CheckConstraint(
            "source_sequence IS NULL OR source_sequence > 0",
            name="ck_context_checkpoint_source_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["conversation_context_checkpoints.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "checkpoint_id",
            "source_kind",
            "source_id",
            name="uq_context_checkpoint_source_identity",
        ),
    )
    sa.Index(
        "ix_context_checkpoint_source_sequence",
        table.c.checkpoint_id,
        table.c.source_sequence,
    )
    return table


def _alter_length(*, old_length: int, new_length: int) -> None:
    if op.get_bind().dialect.name == "sqlite":
        # Some recognized alpha databases legitimately lack the tables that a
        # checkpoint's parent FK references. Supplying the known local table
        # avoids recursive reflection while preserving this table's own FK,
        # checks, unique identity and index during SQLite's table rebuild.
        with op.batch_alter_table(
            _TABLE,
            recreate="always",
            copy_from=_sqlite_source_table(source_id_length=old_length),
        ) as batch:
            batch.alter_column(
                _COLUMN,
                existing_type=sa.String(length=old_length),
                type_=sa.String(length=new_length),
                existing_nullable=False,
            )
        return
    op.alter_column(
        _TABLE,
        _COLUMN,
        existing_type=sa.String(length=old_length),
        type_=sa.String(length=new_length),
        existing_nullable=False,
    )


def upgrade() -> None:
    current = _source_id_length()
    if current == _NEW_LENGTH:
        return
    if current != _OLD_LENGTH:
        raise RuntimeError(
            f"Migration drift: expected {_TABLE}.{_COLUMN} VARCHAR({_OLD_LENGTH}), "
            f"found length {current!r}"
        )
    _alter_length(old_length=_OLD_LENGTH, new_length=_NEW_LENGTH)


def downgrade() -> None:
    current = _source_id_length()
    if current == _OLD_LENGTH:
        return
    if current != _NEW_LENGTH:
        raise RuntimeError(
            f"Migration drift: expected {_TABLE}.{_COLUMN} VARCHAR({_NEW_LENGTH}), "
            f"found length {current!r}"
        )
    oversized = (
        op.get_bind()
        .execute(
            sa.text(f'SELECT COUNT(*) FROM "{_TABLE}" WHERE LENGTH("{_COLUMN}") > {_OLD_LENGTH}')
        )
        .scalar_one()
    )
    if int(oversized or 0) != 0:
        raise RuntimeError(
            "Cannot downgrade checkpoint source IDs to VARCHAR(36): "
            "canonical Creation source-run IDs are present"
        )
    _alter_length(old_length=_NEW_LENGTH, new_length=_OLD_LENGTH)
