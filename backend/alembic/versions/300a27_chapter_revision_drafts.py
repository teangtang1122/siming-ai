"""Add reviewable revision identity to generated chapter drafts.

Revision ID: 300a27_chapter_revision_drafts
Revises: 300a26_outline_drafts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a27_chapter_revision_drafts"
down_revision = "300a26_outline_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chapter_drafts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chapter_drafts")}
    if "draft_kind" not in columns:
        op.add_column(
            "chapter_drafts",
            sa.Column("draft_kind", sa.String(length=20), nullable=False, server_default="new"),
        )
    if "target_chapter_id" not in columns:
        op.add_column(
            "chapter_drafts",
            sa.Column("target_chapter_id", sa.String(length=36), nullable=True),
        )
    if "base_chapter_version" not in columns:
        op.add_column(
            "chapter_drafts",
            sa.Column("base_chapter_version", sa.Integer(), nullable=True),
        )
    foreign_keys = sa.inspect(bind).get_foreign_keys("chapter_drafts")
    if not any(
        key.get("referred_table") == "chapters"
        and key.get("constrained_columns") == ["target_chapter_id"]
        for key in foreign_keys
    ):
        with op.batch_alter_table("chapter_drafts") as batch_op:
            batch_op.create_foreign_key(
                "fk_chapter_drafts_target_chapter",
                "chapters",
                ["target_chapter_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chapter_drafts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chapter_drafts")}
    foreign_keys = inspector.get_foreign_keys("chapter_drafts")
    with op.batch_alter_table("chapter_drafts") as batch_op:
        if any(key.get("name") == "fk_chapter_drafts_target_chapter" for key in foreign_keys):
            batch_op.drop_constraint(
                "fk_chapter_drafts_target_chapter",
                type_="foreignkey",
            )
        if "base_chapter_version" in columns:
            batch_op.drop_column("base_chapter_version")
        if "target_chapter_id" in columns:
            batch_op.drop_column("target_chapter_id")
        if "draft_kind" in columns:
            batch_op.drop_column("draft_kind")
