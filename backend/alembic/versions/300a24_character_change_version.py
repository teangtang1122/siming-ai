"""Add the missing chapter version to character change logs.

Revision ID: 300a24_character_change_version
Revises: 300a23_project_package_receipts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a24_character_change_version"
down_revision = "300a23_project_package_receipts"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "character_change_logs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "chapter_version" not in _columns("character_change_logs"):
        # Existing logs predate version capture, so their version must remain
        # unknown instead of being inferred from the chapter's current state.
        op.add_column(
            "character_change_logs",
            sa.Column("chapter_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    if "character_change_logs" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if "chapter_version" in _columns("character_change_logs"):
        op.drop_column("character_change_logs", "chapter_version")
