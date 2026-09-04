"""Add explicit, device-scoped assistant transcript imports.

Revision ID: 300a29_transcript_imports
Revises: 300a28_conversation_context
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a29_transcript_imports"
down_revision = "300a28_conversation_context"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _create_replicas() -> None:
    if "assistant_conversation_replicas" in _tables():
        return
    op.create_table(
        "assistant_conversation_replicas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_conversation_id", sa.String(length=36), nullable=False),
        sa.Column("device_scope", sa.String(length=128), nullable=False),
        sa.Column("client_conversation_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assistant_conversation_id"],
            ["assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assistant_conversation_id",
            name="uq_assistant_conversation_replica_server",
        ),
        sa.UniqueConstraint(
            "device_scope",
            "client_conversation_id",
            name="uq_assistant_conversation_replica_client",
        ),
    )
    op.create_index(
        "ix_assistant_conversation_replica_owner",
        "assistant_conversation_replicas",
        ["project_id", "device_scope"],
    )


def _create_receipts() -> None:
    if "assistant_transcript_import_receipts" in _tables():
        return
    op.create_table(
        "assistant_transcript_import_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_conversation_id", sa.String(length=36), nullable=False),
        sa.Column("replica_id", sa.String(length=36), nullable=False),
        sa.Column("device_scope", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("source_transcript_revision", sa.Integer(), nullable=False),
        sa.Column("source_first_sequence", sa.Integer(), nullable=False),
        sa.Column("source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("imported_message_count", sa.Integer(), nullable=False),
        sa.Column("result_transcript_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_transcript_revision > 0 "
            "AND source_first_sequence > 0 "
            "AND source_last_sequence >= source_first_sequence "
            "AND source_message_count > 0 "
            "AND imported_message_count >= 0 "
            "AND result_transcript_revision >= source_last_sequence",
            name="ck_assistant_transcript_import_ranges",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assistant_conversation_id"],
            ["assistant_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replica_id"],
            ["assistant_conversation_replicas.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_scope",
            "idempotency_key",
            name="uq_assistant_transcript_import_device_key",
        ),
    )
    op.create_index(
        "ix_assistant_transcript_import_conversation",
        "assistant_transcript_import_receipts",
        ["assistant_conversation_id", "created_at"],
    )


def upgrade() -> None:
    _create_replicas()
    _create_receipts()


def downgrade() -> None:
    tables = _tables()
    if "assistant_transcript_import_receipts" in tables:
        op.drop_table("assistant_transcript_import_receipts")
    if "assistant_conversation_replicas" in tables:
        op.drop_table("assistant_conversation_replicas")
