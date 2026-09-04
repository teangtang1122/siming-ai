"""Canonicalize legacy provider model identities.

Revision ID: 300a34_canonical_model_identity
Revises: 300a33_legacy_message_integrity
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a34_canonical_model_identity"
down_revision = "300a33_legacy_message_integrity"
branch_labels = None
depends_on = None

_LEGACY_DEEPSEEK_MODEL = "deepseek-v3"
_CANONICAL_DEEPSEEK_MODEL = "deepseek-v4-flash"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _canonicalize_column(table: str, column: str) -> None:
    if table not in _tables():
        return
    op.get_bind().execute(
        sa.text(
            f'UPDATE "{table}" SET "{column}" = :canonical '
            "WHERE LOWER(TRIM(provider)) = 'deepseek' "
            f'AND TRIM("{column}") = :legacy'
        ),
        {
            "canonical": _CANONICAL_DEEPSEEK_MODEL,
            "legacy": _LEGACY_DEEPSEEK_MODEL,
        },
    )


def _canonicalize_profiles() -> None:
    table = "model_context_profiles"
    if table not in _tables():
        return
    connection = op.get_bind()
    # A 3.3.11 author may already have saved the canonical profile manually.
    # Keep that row authoritative and remove only its now-redundant alias row.
    connection.execute(
        sa.text(
            'DELETE FROM "model_context_profiles" '
            "WHERE LOWER(TRIM(provider)) = 'deepseek' "
            "AND TRIM(model_name) = :legacy "
            "AND EXISTS ("
            'SELECT 1 FROM "model_context_profiles" AS canonical '
            "WHERE LOWER(TRIM(canonical.provider)) = 'deepseek' "
            "AND TRIM(canonical.model_name) = :canonical"
            ")"
        ),
        {
            "canonical": _CANONICAL_DEEPSEEK_MODEL,
            "legacy": _LEGACY_DEEPSEEK_MODEL,
        },
    )
    _canonicalize_column(table, "model_name")


def _canonicalize_conversations() -> None:
    table = "assistant_conversations"
    if table not in _tables():
        return
    op.get_bind().execute(
        sa.text(
            'UPDATE "assistant_conversations" SET model = :canonical '
            "WHERE LOWER(TRIM(model)) = :legacy"
        ),
        {
            "canonical": f"deepseek:{_CANONICAL_DEEPSEEK_MODEL}",
            "legacy": f"deepseek:{_LEGACY_DEEPSEEK_MODEL}",
        },
    )


def upgrade() -> None:
    _canonicalize_column("api_configs", "default_model")
    _canonicalize_column("model_task_settings", "model_name")
    _canonicalize_profiles()
    _canonicalize_conversations()


def downgrade() -> None:
    # This normalization cannot distinguish a legacy alias from an author who
    # independently chose the canonical model. Runtime code remains able to
    # read either identity, so downgrading the schema needs no data rewrite.
    return
