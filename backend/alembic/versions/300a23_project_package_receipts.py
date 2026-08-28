"""Add idempotency receipts for strict project-package imports.

Revision ID: 300a23_project_package_receipts
Revises: 300a22_provider_task_models
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a23_project_package_receipts"
down_revision = "300a22_provider_task_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "project_package_import_receipts" in tables:
        return
    op.create_table(
        "project_package_import_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("requested_title", sa.String(length=200), nullable=True),
        sa.Column("package_id", sa.String(length=36), nullable=False),
        sa.Column("profile", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_project_package_import_receipt_key",
        ),
    )
    op.create_index(
        "ix_project_package_import_receipt_project",
        "project_package_import_receipts",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "project_package_import_receipts" in tables:
        op.drop_table("project_package_import_receipts")
