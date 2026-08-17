"""Give chapters an independent canonical reading order.

Revision ID: 300a17_chapter_sort_order
Revises: 300a16_character_role_type_enum
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "300a17_chapter_sort_order"
down_revision = "300a16_character_role_type_enum"
branch_labels = None
depends_on = None


def _time_key(value):
    return value or datetime.min


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    # Some historical/fixture databases are intentionally partial but already
    # carry an Alembic revision. They must still advance safely to head.
    if "chapters" not in tables:
        return

    # The 3.0 baseline reconciler uses current SQLAlchemy metadata when it sees
    # an unversioned/fresh database. In that path the new column may already
    # exist before this explicit revision runs; versioned 300a16 databases still
    # need the normal ALTER TABLE.
    chapter_columns = {column["name"] for column in inspector.get_columns("chapters")}
    if "sort_order" not in chapter_columns:
        op.add_column(
            "chapters",
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1000000000"),
        )

    chapters = sa.table(
        "chapters",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("outline_node_id", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )
    outlines = sa.table(
        "outline_nodes",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("parent_id", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )

    project_ids = [
        row[0]
        for row in bind.execute(sa.select(chapters.c.project_id).distinct()).all()
        if row[0]
    ]
    for project_id in project_ids:
        outline_rows = []
        if "outline_nodes" in tables:
            outline_rows = bind.execute(
                sa.select(
                    outlines.c.id,
                    outlines.c.parent_id,
                    outlines.c.sort_order,
                    outlines.c.created_at,
                ).where(outlines.c.project_id == project_id)
            ).mappings().all()
        children: dict[str | None, list] = {}
        for row in outline_rows:
            children.setdefault(row["parent_id"], []).append(row)
        for siblings in children.values():
            siblings.sort(
                key=lambda row: (
                    row["sort_order"] or 0,
                    _time_key(row["created_at"]),
                    str(row["id"]),
                )
            )

        outline_keys: dict[str, tuple[int, ...]] = {}

        def walk(parent_id: str | None, prefix: tuple[int, ...]) -> None:
            for index, row in enumerate(children.get(parent_id, [])):
                key = (*prefix, index)
                outline_keys[str(row["id"])] = key
                walk(str(row["id"]), key)

        walk(None, ())
        chapter_rows = bind.execute(
            sa.select(
                chapters.c.id,
                chapters.c.outline_node_id,
                chapters.c.created_at,
            ).where(chapters.c.project_id == project_id)
        ).mappings().all()

        def old_pc_sort_key(row):
            outline_key = (
                outline_keys.get(str(row["outline_node_id"]))
                if row["outline_node_id"]
                else None
            )
            if outline_key is None:
                return (1, (999999,), _time_key(row["created_at"]), str(row["id"]))
            return (0, outline_key, _time_key(row["created_at"]), str(row["id"]))

        for index, row in enumerate(sorted(chapter_rows, key=old_pc_sort_key), start=1):
            bind.execute(
                chapters.update()
                .where(chapters.c.id == row["id"])
                .values(sort_order=index * 1000)
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chapters" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("chapters")}
    if "sort_order" in columns:
        op.drop_column("chapters", "sort_order")
