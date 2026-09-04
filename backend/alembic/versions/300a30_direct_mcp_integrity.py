"""Add durable Direct-MCP lease and call identities.

Revision ID: 300a30_direct_mcp_integrity
Revises: 300a29_transcript_imports
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a30_direct_mcp_integrity"
down_revision = "300a29_transcript_imports"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table)}


def _indexes(table: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return {}
    return {
        str(index["name"]): dict(index)
        for index in inspector.get_indexes(table)
        if index.get("name")
    }


def _ensure_unique_index(table: str, name: str, columns: list[str]) -> None:
    existing = _indexes(table).get(name)
    if existing is None:
        op.create_index(name, table, columns, unique=True)
        return
    actual_columns = [str(column) for column in existing.get("column_names") or []]
    if not bool(existing.get("unique")) or actual_columns != columns:
        raise RuntimeError(
            f"Migration drift: {name} must be UNIQUE on {table}{tuple(columns)}"
        )


def upgrade() -> None:
    run_columns = _columns("assistant_runs")
    if run_columns:
        if "direct_mcp_lease_hash" not in run_columns:
            op.add_column(
                "assistant_runs",
                sa.Column("direct_mcp_lease_hash", sa.String(length=64), nullable=True),
            )
        if "direct_mcp_lease_iteration" not in run_columns:
            op.add_column(
                "assistant_runs",
                sa.Column("direct_mcp_lease_iteration", sa.Integer(), nullable=True),
            )
        _ensure_unique_index(
            "assistant_runs",
            "uq_assistant_runs_direct_mcp_lease_hash",
            ["direct_mcp_lease_hash"],
        )

    step_columns = _columns("assistant_run_steps")
    if step_columns and "direct_mcp_call_key" not in step_columns:
        op.add_column(
            "assistant_run_steps",
            sa.Column("direct_mcp_call_key", sa.String(length=200), nullable=True),
        )
    if step_columns:
        _ensure_unique_index(
            "assistant_run_steps",
            "uq_run_steps_direct_mcp_call_key",
            ["direct_mcp_call_key"],
        )


def downgrade() -> None:
    if "uq_run_steps_direct_mcp_call_key" in _indexes("assistant_run_steps"):
        op.drop_index(
            "uq_run_steps_direct_mcp_call_key",
            table_name="assistant_run_steps",
        )
    step_columns = _columns("assistant_run_steps")
    if "direct_mcp_call_key" in step_columns:
        op.drop_column("assistant_run_steps", "direct_mcp_call_key")
    run_columns = _columns("assistant_runs")
    if "uq_assistant_runs_direct_mcp_lease_hash" in _indexes("assistant_runs"):
        op.drop_index(
            "uq_assistant_runs_direct_mcp_lease_hash",
            table_name="assistant_runs",
        )
    if "direct_mcp_lease_iteration" in run_columns:
        op.drop_column("assistant_runs", "direct_mcp_lease_iteration")
    if "direct_mcp_lease_hash" in run_columns:
        op.drop_column("assistant_runs", "direct_mcp_lease_hash")
