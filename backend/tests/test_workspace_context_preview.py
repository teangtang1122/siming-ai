"""Tests for workspace writing context preview tool."""

import asyncio
import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from app.database.models import (
    Chapter,
    ChapterSummary,
    Character,
    CharacterAlias,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
)
from app.database.session import Base, SessionLocal, engine
from app.services.workspace.executor import execute_workspace_action


class WorkspaceContextPreviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            for table in [ChapterSummary, Chapter, CharacterAlias, OutlineNodeCharacter, WorldbuildingEntry, Character, OutlineNode, Project]:
                db.query(table).delete()
            db.commit()
        finally:
            db.close()

    def test_preview_writing_context_returns_state_and_warnings(self):
        db = SessionLocal()
        try:
            project = Project(title="预检测试")
            db.add(project)
            db.flush()
            outline = OutlineNode(project_id=project.id, title="第151章 众生相", node_type="chapter", summary="外界视角展现病毒回收。")
            char = Character(
                project_id=project.id,
                name="特昂糖",
                role_type="protagonist",
                current_location="归寂谷阵屋",
                realm_or_level="经脉受损，靠合击阵借力",
                physical_state="脸色苍白，经脉无法自主运转。",
                current_goal="在归墟阵耗尽前找出病毒主脑。",
            )
            db.add_all([outline, char])
            db.flush()
            db.add(OutlineNodeCharacter(outline_node_id=outline.id, character_id=char.id, role_in_scene="主视角"))
            db.add(CharacterAlias(project_id=project.id, character_id=char.id, alias="陆糖", alias_type="alias"))
            db.add(WorldbuildingEntry(
                project_id=project.id,
                dimension="power_system",
                title="归墟阵",
                content="归墟阵以黄泉死气和大日生气维持封印，可承载血魔封锁。",
            ))
            chapter = Chapter(project_id=project.id, title="第150章 死线蔓延", content="前文", word_count=2)
            db.add(chapter)
            db.flush()
            db.add(ChapterSummary(chapter_id=chapter.id, summary_text="病毒网络开始回收，黑线指向北邙山。"))
            db.commit()

            result = asyncio.run(execute_workspace_action(db, project.id, {
                "tool": "preview_writing_context",
                "arguments": {
                    "outline_node_id": outline.id,
                    "requirements": "写第151章，外界视角，病毒回收",
                    "involved_characters": ["陆糖", "不存在的人"],
                },
            }))

            self.assertEqual(result["status"], "ok")
            data = result["data"]
            self.assertEqual(data["characters"][0]["name"], "特昂糖")
            self.assertIn("陆糖", data["characters"][0]["aliases"])
            self.assertIn("归寂谷", data["characters"][0]["current_location"])
            self.assertIn("第150章", data["recent_chapters"][0]["title"])
            self.assertTrue(any("不存在的人" in warning for warning in data["warnings"]))
            self.assertIn("归墟阵", data["world_context"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
