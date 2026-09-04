from __future__ import annotations

from sqlalchemy import create_engine, text

from alembic import command
from app.database.bootstrap import alembic_config


def test_migration_promotes_actual_outline_without_moving_structural_slot(tmp_path):
    database_path = tmp_path / "outline-projection.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = alembic_config(url)
    command.upgrade(config, "300a35_relationship_integrity")
    engine = create_engine(url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, title, created_at, updated_at) "
                "VALUES ('project-1', '大纲迁移', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO outline_nodes "
                "(id, project_id, node_type, title, summary, actual_summary, "
                "planned_summary, status, cataloging_status, sort_order, created_at, updated_at) "
                "VALUES "
                "('volume-1', 'project-1', 'volume', '第一卷', '', NULL, NULL, "
                "'pending', NULL, 1000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('outline-1', 'project-1', 'chapter', '立项标题', '立项摘要', "
                "'正文实际摘要', '立项摘要', 'completed', 'cataloged', 7300, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('planning-section', 'project-1', 'section', '立项场景', '旧规划', "
                "NULL, NULL, 'pending', NULL, 1000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "UPDATE outline_nodes SET parent_id = 'volume-1' WHERE id = 'outline-1'"
            )
        )
        connection.execute(
            text(
                "UPDATE outline_nodes SET parent_id = 'outline-1' "
                "WHERE id = 'planning-section'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chapters "
                "(id, project_id, outline_node_id, title, content, word_count, current_version, "
                "cataloging_required, sort_order, created_at, updated_at) "
                "VALUES ('chapter-1', 'project-1', 'outline-1', '正文标题', '正文', 2, 1, "
                "0, 22000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(config, "300a36_outline_projection_identity")

    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT title, summary, actual_summary, planned_summary, parent_id, "
                "source_chapter_id, sort_order FROM outline_nodes WHERE id = 'outline-1'"
            )
        ).mappings().one()
        assert dict(row) == {
            "title": "立项标题",
            "summary": "正文实际摘要",
            "actual_summary": "正文实际摘要",
            "planned_summary": "立项摘要",
            "parent_id": "volume-1",
            "source_chapter_id": "chapter-1",
            "sort_order": 7300,
        }
        assert connection.execute(
            text("SELECT COUNT(*) FROM outline_nodes WHERE id = 'planning-section'")
        ).scalar_one() == 0
    engine.dispose()
