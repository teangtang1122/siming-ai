"""Persist ordered Agent messages and conversation context checkpoints.

Revision ID: 300a28_conversation_context
Revises: 300a27_chapter_revision_drafts
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa

from alembic import op

revision = "300a28_conversation_context"
down_revision = "300a27_chapter_revision_drafts"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table_name: str) -> dict[str, dict]:
    return {column["name"]: column for column in _inspector().get_columns(table_name)}


def _unique_names(table_name: str) -> set[str]:
    return {
        str(constraint.get("name"))
        for constraint in _inspector().get_unique_constraints(table_name)
        if constraint.get("name")
    }


def _check_names(table_name: str) -> set[str]:
    return {
        str(constraint.get("name"))
        for constraint in _inspector().get_check_constraints(table_name)
        if constraint.get("name")
    }


def _index_names(table_name: str) -> set[str]:
    return {
        str(index.get("name"))
        for index in _inspector().get_indexes(table_name)
        if index.get("name")
    }


def _backfill_sequence(table_name: str) -> None:
    """Backfill deterministic per-conversation order without timestamp ties."""

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The legacy SQLite writers inserted each native user/assistant pair
        # atomically, while their second-resolution timestamps may tie across
        # several turns.  rowid is the only surviving source-order signal;
        # sorting by role would incorrectly group all users before assistants.
        order_by = "conversation_id ASC, rowid ASC"
    else:
        # Other supported dialects do not expose SQLite rowid.  Keep a stable,
        # role-neutral ordering so timestamp ties never group messages by role.
        order_by = "conversation_id ASC, created_at ASC, updated_at ASC, id ASC"
    rows = bind.execute(
        sa.text(f'SELECT id, conversation_id FROM "{table_name}" ORDER BY {order_by}')
    )
    current_conversation: str | None = None
    sequence_no = 0
    batch: list[dict[str, object]] = []
    statement = sa.text(
        f'UPDATE "{table_name}" SET sequence_no = :sequence_no WHERE id = :message_id'
    )
    for row in rows:
        conversation_id = str(row.conversation_id)
        if conversation_id != current_conversation:
            current_conversation = conversation_id
            sequence_no = 0
        sequence_no += 1
        batch.append({"message_id": row.id, "sequence_no": sequence_no})
        if len(batch) >= 1000:
            bind.execute(statement, batch)
            batch.clear()
    if batch:
        bind.execute(statement, batch)


def _sequence_backfill_required(table_name: str) -> bool:
    """Protect an already authoritative sequence from partial-migration replay."""

    rows = list(
        op.get_bind().execute(
            sa.text(f'SELECT conversation_id, sequence_no FROM "{table_name}"')
        )
    )
    if not rows:
        return False
    values = [row.sequence_no for row in rows]
    if all(value is None for value in values):
        return True
    if any(value is None for value in values):
        raise RuntimeError(
            f"{table_name}.sequence_no is partially populated; refusing to reorder messages"
        )

    by_conversation: dict[str, list[int]] = {}
    for row in rows:
        by_conversation.setdefault(str(row.conversation_id), []).append(int(row.sequence_no))
    for conversation_id, sequences in by_conversation.items():
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise RuntimeError(
                f"{table_name}.sequence_no is invalid for conversation {conversation_id}; "
                "refusing to reorder messages"
            )
    return False


def _ensure_message_sequence(
    table_name: str,
    *,
    unique_name: str,
    check_name: str,
) -> None:
    if table_name not in _tables():
        return
    columns = _columns(table_name)
    if "sequence_no" not in columns:
        op.add_column(table_name, sa.Column("sequence_no", sa.Integer(), nullable=True))
        columns = _columns(table_name)

    unique_missing = unique_name not in _unique_names(table_name)
    check_missing = check_name not in _check_names(table_name)
    nullable = bool(columns["sequence_no"].get("nullable", True))
    if (nullable or unique_missing) and _sequence_backfill_required(table_name):
        _backfill_sequence(table_name)

    if nullable or unique_missing or check_missing:
        with op.batch_alter_table(table_name) as batch:
            if nullable:
                batch.alter_column(
                    "sequence_no",
                    existing_type=sa.Integer(),
                    nullable=False,
                )
            if unique_missing:
                batch.create_unique_constraint(
                    unique_name,
                    ["conversation_id", "sequence_no"],
                )
            if check_missing:
                batch.create_check_constraint(check_name, "sequence_no > 0")


def _ensure_indexes(table_name: str, indexes: Iterable[tuple[str, list[str]]]) -> None:
    existing = _index_names(table_name)
    for name, columns in indexes:
        if name not in existing:
            op.create_index(name, table_name, columns)


def _create_checkpoint_table() -> None:
    if "conversation_context_checkpoints" in _tables():
        return
    op.create_table(
        "conversation_context_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_kind", sa.String(length=20), nullable=False),
        sa.Column("assistant_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("system_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("parent_checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_first_sequence", sa.Integer(), nullable=False),
        sa.Column("source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("transcript_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("model_binding_json", sa.JSON(), nullable=False),
        sa.Column("model_binding_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("semantic_navigation_json", sa.JSON(), nullable=False),
        sa.Column("author_quotes_json", sa.JSON(), nullable=False),
        sa.Column("execution_ledger_json", sa.JSON(), nullable=False),
        sa.Column("project_refs_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("original_tokens", sa.Integer(), nullable=True),
        sa.Column("checkpoint_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "((conversation_kind = 'workspace' "
            "AND assistant_conversation_id IS NOT NULL "
            "AND system_conversation_id IS NULL) "
            "OR (conversation_kind = 'creation' "
            "AND assistant_conversation_id IS NULL "
            "AND system_conversation_id IS NOT NULL))",
            name="ck_context_checkpoint_conversation_owner",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'compressing', 'ready', 'failed', 'cancelled', 'superseded')",
            name="ck_context_checkpoint_status",
        ),
        sa.CheckConstraint(
            "source_first_sequence > 0 "
            "AND source_last_sequence >= source_first_sequence "
            "AND source_message_count > 0",
            name="ck_context_checkpoint_source_range",
        ),
        sa.CheckConstraint(
            "policy_version > 0 AND transcript_revision >= 0",
            name="ck_context_checkpoint_versions",
        ),
        sa.CheckConstraint(
            "(original_tokens IS NULL OR original_tokens >= 0) "
            "AND (checkpoint_tokens IS NULL OR checkpoint_tokens >= 0)",
            name="ck_context_checkpoint_tokens",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_conversation_id"],
            ["assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["system_conversation_id"],
            ["system_assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_checkpoint_id"],
            ["conversation_context_checkpoints.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_context_checkpoint_idempotency"),
    )


def _create_checkpoint_sources_table() -> None:
    if "conversation_context_checkpoint_sources" in _tables():
        return
    op.create_table(
        "conversation_context_checkpoint_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
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


def _create_context_state_table() -> None:
    if "conversation_context_states" in _tables():
        return
    op.create_table(
        "conversation_context_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_kind", sa.String(length=20), nullable=False),
        sa.Column("assistant_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("system_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active_checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("active_source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_budget_json", sa.JSON(), nullable=False),
        sa.Column("last_compacted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "((conversation_kind = 'workspace' "
            "AND assistant_conversation_id IS NOT NULL "
            "AND system_conversation_id IS NULL) "
            "OR (conversation_kind = 'creation' "
            "AND assistant_conversation_id IS NULL "
            "AND system_conversation_id IS NOT NULL))",
            name="ck_context_state_conversation_owner",
        ),
        sa.CheckConstraint(
            "revision >= 0 AND active_source_last_sequence >= 0",
            name="ck_context_state_versions",
        ),
        sa.ForeignKeyConstraint(
            ["active_checkpoint_id"],
            ["conversation_context_checkpoints.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_conversation_id"],
            ["assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["system_conversation_id"],
            ["system_assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assistant_conversation_id",
            name="uq_context_state_assistant_conversation",
        ),
        sa.UniqueConstraint(
            "system_conversation_id",
            name="uq_context_state_system_conversation",
        ),
    )


def upgrade() -> None:
    _ensure_message_sequence(
        "assistant_messages",
        unique_name="uq_assistant_messages_conversation_sequence",
        check_name="ck_assistant_messages_sequence_positive",
    )
    _ensure_message_sequence(
        "system_assistant_messages",
        unique_name="uq_system_assistant_messages_conversation_sequence",
        check_name="ck_system_assistant_messages_sequence_positive",
    )
    _create_checkpoint_table()
    _create_checkpoint_sources_table()
    _create_context_state_table()
    _ensure_indexes(
        "conversation_context_checkpoints",
        (
            (
                "ix_context_checkpoint_assistant_status",
                ["assistant_conversation_id", "status", "created_at"],
            ),
            (
                "ix_context_checkpoint_system_status",
                ["system_conversation_id", "status", "created_at"],
            ),
            ("ix_context_checkpoint_parent", ["parent_checkpoint_id"]),
        ),
    )
    _ensure_indexes(
        "conversation_context_checkpoint_sources",
        (
            (
                "ix_context_checkpoint_source_sequence",
                ["checkpoint_id", "source_sequence"],
            ),
        ),
    )
    _ensure_indexes(
        "conversation_context_states",
        (("ix_context_state_active_checkpoint", ["active_checkpoint_id"]),),
    )


def _drop_message_sequence(
    table_name: str,
    *,
    unique_name: str,
    check_name: str,
) -> None:
    if table_name not in _tables() or "sequence_no" not in _columns(table_name):
        return
    unique_names = _unique_names(table_name)
    check_names = _check_names(table_name)
    with op.batch_alter_table(table_name) as batch:
        if unique_name in unique_names:
            batch.drop_constraint(unique_name, type_="unique")
        if check_name in check_names:
            batch.drop_constraint(check_name, type_="check")
        batch.drop_column("sequence_no")


def downgrade() -> None:
    for table_name in (
        "conversation_context_checkpoint_sources",
        "conversation_context_states",
        "conversation_context_checkpoints",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
    _drop_message_sequence(
        "system_assistant_messages",
        unique_name="uq_system_assistant_messages_conversation_sequence",
        check_name="ck_system_assistant_messages_sequence_positive",
    )
    _drop_message_sequence(
        "assistant_messages",
        unique_name="uq_assistant_messages_conversation_sequence",
        check_name="ck_assistant_messages_sequence_positive",
    )
