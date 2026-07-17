"""Add the durable content mirror synchronization outbox.

Revision ID: 300a2_content_sync
Revises: 300a1_baseline
Create Date: 2026-07-17
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a2_content_sync"
down_revision = "300a1_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "content_sync_jobs" not in inspector.get_table_names():
        op.create_table(
            "content_sync_jobs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("target", sa.String(length=50), nullable=False),
            sa.Column("entity_id", sa.String(length=36), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("dedupe_key", sa.String(length=500), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)

    existing_indexes = {
        index["name"] for index in inspector.get_indexes("content_sync_jobs")
    }
    indexes = {
        "ix_content_sync_jobs_status_next": ["status", "next_attempt_at"],
        "ix_content_sync_jobs_project_created": ["project_id", "created_at"],
        "ix_content_sync_jobs_dedupe": ["dedupe_key", "status"],
    }
    for name, columns in indexes.items():
        if name not in existing_indexes:
            op.create_index(name, "content_sync_jobs", columns)


def downgrade() -> None:
    op.drop_index("ix_content_sync_jobs_dedupe", table_name="content_sync_jobs")
    op.drop_index("ix_content_sync_jobs_project_created", table_name="content_sync_jobs")
    op.drop_index("ix_content_sync_jobs_status_next", table_name="content_sync_jobs")
    op.drop_table("content_sync_jobs")
