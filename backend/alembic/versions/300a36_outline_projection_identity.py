"""Promote cataloged chapter projections to the visible outline state.

Revision ID: 300a36_outline_projection_identity
Revises: 300a35_relationship_integrity
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a36_outline_projection_identity"
down_revision = "300a35_relationship_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    if not {"chapters", "outline_nodes"} <= tables:
        return
    connection.execute(
        sa.text(
            """
            UPDATE outline_nodes
            SET summary = actual_summary,
                source_chapter_id = (
                    SELECT chapters.id
                    FROM chapters
                    WHERE chapters.outline_node_id = outline_nodes.id
                    ORDER BY chapters.sort_order, chapters.created_at, chapters.id
                    LIMIT 1
                )
            WHERE node_type = 'chapter'
              AND cataloging_status = 'cataloged'
              AND actual_summary IS NOT NULL
              AND TRIM(actual_summary) <> ''
              AND EXISTS (
                  SELECT 1
                  FROM chapters
                  WHERE chapters.outline_node_id = outline_nodes.id
              )
            """
        )
    )
    # Completed chapter projections replace initial planning scenes.  Older
    # builds kept those source-less children beside the generated scenes.
    connection.execute(
        sa.text(
            """
            DELETE FROM outline_nodes
            WHERE node_type = 'section'
              AND source_chapter_id IS NULL
              AND parent_id IN (
                  SELECT outline_nodes.id
                  FROM outline_nodes
                  JOIN chapters ON chapters.outline_node_id = outline_nodes.id
                  WHERE outline_nodes.node_type = 'chapter'
                    AND outline_nodes.cataloging_status = 'cataloged'
                    AND outline_nodes.actual_summary IS NOT NULL
                    AND TRIM(outline_nodes.actual_summary) <> ''
              )
            """
        )
    )


def downgrade() -> None:
    # This is a projection data repair.  planned_summary remains intact, but
    # the previous visible-summary choice cannot be inferred for every row.
    pass
