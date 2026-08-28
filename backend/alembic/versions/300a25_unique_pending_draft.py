"""Keep one author-visible pending chapter draft per project.

Revision ID: 300a25_unique_pending_draft
Revises: 300a24_character_change_version
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "300a25_unique_pending_draft"
down_revision = "300a24_character_change_version"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_chapter_drafts_project_pending"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "chapter_drafts" not in tables:
        return

    # The 3.0 baseline reconciler can create a plain index for a newly added
    # model index on a legacy table. Recreate it here with its authoritative
    # unique/partial semantics after repairing historical rows.
    if INDEX_NAME in {index["name"] for index in inspector.get_indexes("chapter_drafts")}:
        op.drop_index(INDEX_NAME, table_name="chapter_drafts")

    drafts = sa.table(
        "chapter_drafts",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("outline_node_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )

    pending_rows = bind.execute(
        sa.select(
            drafts.c.id,
            drafts.c.project_id,
            drafts.c.outline_node_id,
            drafts.c.created_at,
            drafts.c.updated_at,
        ).where(drafts.c.status == "pending")
    ).mappings().all()

    stale_ids: set[str] = set()
    if "chapters" in tables:
        chapters = sa.table(
            "chapters",
            sa.column("project_id", sa.String()),
            sa.column("outline_node_id", sa.String()),
        )
        used_outlines = {
            (str(row["project_id"]), str(row["outline_node_id"]))
            for row in bind.execute(
                sa.select(chapters.c.project_id, chapters.c.outline_node_id).where(
                    chapters.c.outline_node_id.is_not(None)
                )
            ).mappings()
            if row["project_id"] and row["outline_node_id"]
        }
        stale_ids = {
            str(row["id"])
            for row in pending_rows
            if row["outline_node_id"]
            and (str(row["project_id"]), str(row["outline_node_id"])) in used_outlines
        }

    remaining = [row for row in pending_rows if str(row["id"]) not in stale_ids]
    seen_projects: set[str] = set()
    duplicate_ids: set[str] = set()
    for row in sorted(
        remaining,
        key=lambda item: (
            str(item["updated_at"] or ""),
            str(item["created_at"] or ""),
            str(item["id"]),
        ),
        reverse=True,
    ):
        project_id = str(row["project_id"])
        if project_id in seen_projects:
            duplicate_ids.add(str(row["id"]))
        else:
            seen_projects.add(project_id)

    superseded_ids = stale_ids | duplicate_ids
    if superseded_ids:
        bind.execute(
            drafts.update()
            .where(drafts.c.id.in_(superseded_ids))
            .values(status="superseded")
        )

    op.create_index(
        INDEX_NAME,
        "chapter_drafts",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "chapter_drafts" not in inspector.get_table_names():
        return
    if INDEX_NAME in {index["name"] for index in inspector.get_indexes("chapter_drafts")}:
        op.drop_index(INDEX_NAME, table_name="chapter_drafts")
