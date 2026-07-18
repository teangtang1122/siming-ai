"""Establish the Siming 3.0 versioned schema baseline.

Revision ID: 300a1_baseline
Revises:
Create Date: 2026-07-16
"""
from __future__ import annotations

from alembic import op


revision = "300a1_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-3.0 installations were not versioned. Reconcile every known legacy
    # shape once, then let subsequent revisions use explicit Alembic operations.
    from app.database.migrations import reconcile_pre_3_schema
    from app.database import models as _models  # noqa: F401

    reconcile_pre_3_schema(op.get_bind())


def downgrade() -> None:
    # The baseline never removes user data. Rolling back the executable leaves
    # the additive schema readable by 2.9.x.
    pass
