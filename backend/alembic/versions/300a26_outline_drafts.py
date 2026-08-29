"""Persist one author-visible pending outline proposal per project.

Revision ID: 300a26_outline_drafts
Revises: 300a25_unique_pending_draft
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a26_outline_drafts"
down_revision = "300a25_unique_pending_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "outline_drafts" in inspector.get_table_names():
        return
    op.create_table(
        "outline_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("context_manifest_id", sa.String(length=36), nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("insert_after_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("nodes_json", sa.JSON(), nullable=False),
        sa.Column("design_notes", sa.Text(), nullable=False),
        sa.Column("context_selection_digest", sa.String(length=64), nullable=False),
        sa.Column("base_outline_hash", sa.String(length=64), nullable=False),
        sa.Column("saved_outline_node_ids", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["context_manifest_id"], ["context_manifests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_outline_drafts_project_pending",
        "outline_drafts",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "outline_drafts" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("outline_drafts")}
    if "uq_outline_drafts_project_pending" in indexes:
        op.drop_index("uq_outline_drafts_project_pending", table_name="outline_drafts")
    op.drop_table("outline_drafts")
