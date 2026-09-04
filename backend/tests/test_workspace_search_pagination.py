import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, OutlineNode, Project
from app.services.workspace.tools.search import search_outline


def test_search_outline_reports_total_and_replayable_child_pages() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-outline-page", title="分页测试", description="")
        parent = OutlineNode(
            id="parent",
            project_id=project.id,
            node_type="chapter",
            title="第一章",
            sort_order=0,
        )
        children = [
            OutlineNode(
                id="child-b",
                project_id=project.id,
                parent_id=parent.id,
                node_type="section",
                title="场景乙",
                sort_order=1,
            ),
            OutlineNode(
                id="child-a",
                project_id=project.id,
                parent_id=parent.id,
                node_type="section",
                title="场景甲",
                sort_order=1,
            ),
            OutlineNode(
                id="child-c",
                project_id=project.id,
                parent_id=parent.id,
                node_type="section",
                title="场景丙",
                sort_order=2,
            ),
        ]
        db.add_all([project, parent, *children])
        db.flush()

        first = asyncio.run(
            search_outline(
                db,
                project.id,
                {
                    "node_id": parent.id,
                    "limit": 2,
                    "summary_chars": 60,
                    "linked_limit": 1,
                },
            )
        )

        assert first["page"] == {
            "cursor": 0,
            "limit": 2,
            "returned_items": 2,
            "total_items": 3,
            "next_cursor": 2,
            "has_more": True,
        }
        assert "子节点共 3 个，本页返回 2 个" in first["detail"]
        assert [item["id"] for item in first["data"][0]["children"]] == [
            "child-a",
            "child-b",
        ]
        assert first["next_arguments"] == {
            "cursor": 2,
            "limit": 2,
            "summary_offset_chars": 0,
            "summary_chars": 60,
            "linked_cursor": 0,
            "linked_limit": 1,
            "node_id": "parent",
        }

        second = asyncio.run(search_outline(db, project.id, first["next_arguments"]))

        assert second["page"] == {
            "cursor": 2,
            "limit": 2,
            "returned_items": 1,
            "total_items": 3,
            "next_cursor": None,
            "has_more": False,
        }
        assert [item["id"] for item in second["data"][0]["children"]] == ["child-c"]
        assert "next_arguments" not in second
    finally:
        db.close()
        engine.dispose()


def test_search_outline_query_page_distinguishes_total_from_page_size() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-outline-query", title="查询测试", description="")
        db.add(project)
        db.add_all(
            [
                OutlineNode(
                    id=f"scene-{index}",
                    project_id=project.id,
                    node_type="section",
                    title=f"场景{index}",
                    sort_order=index,
                )
                for index in range(3)
            ]
        )
        db.flush()

        result = asyncio.run(
            search_outline(db, project.id, {"query": "场景", "cursor": 0, "limit": 2})
        )

        assert result["page"]["total_items"] == 3
        assert result["page"]["returned_items"] == 2
        assert result["next_arguments"]["query"] == "场景"
        assert "匹配大纲节点共 3 个，本页返回 2 个" in result["detail"]
    finally:
        db.close()
        engine.dispose()
