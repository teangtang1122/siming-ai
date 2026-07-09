"""Tests for system-management workspace tools exposed to the assistant."""

import os
import tempfile
import unittest

os.environ["DATABASE_URL"] = "sqlite:///./test_workspace_system_tools.db"

from app.database.models import Chapter, ChapterSnapshot, Character, Project, ScheduledTask, Skill
from app.database.session import Base, SessionLocal, engine
from app.services.agent.planner import build_plan_from_intent, detect_intent
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry


class WorkspaceSystemToolsTestCase(unittest.IsolatedAsyncioTestCase):
    """Assistant tools for system management should be callable and durable."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_workspace_system_tools.db")
        except OSError:
            pass

    def setUp(self):
        self.db = SessionLocal()
        self.db.query(ScheduledTask).delete()
        self.db.query(Skill).delete()
        self.db.query(ChapterSnapshot).delete()
        self.db.query(Chapter).delete()
        self.db.query(Character).delete()
        self.db.query(Project).delete()
        self.project = Project(title="系统工具测试作品")
        self.db.add(self.project)
        self.db.commit()
        self.db.refresh(self.project)

    def tearDown(self):
        self.db.close()

    async def test_agent_can_create_scheduled_task(self):
        result = await execute_workspace_action(
            self.db,
            self.project.id,
            {
                "tool": "create_scheduled_task",
                "arguments": {
                    "name": "每日资料整理",
                    "prompt": "每天整理一次写作资料。",
                    "interval_minutes": 60,
                },
            },
        )
        self.db.commit()

        self.assertEqual(result["status"], "ok")
        task = self.db.query(ScheduledTask).filter(ScheduledTask.project_id == self.project.id).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.name, "每日资料整理")

    async def test_agent_can_create_skill_from_requirements(self):
        result = await execute_workspace_action(
            self.db,
            self.project.id,
            {
                "tool": "create_skill",
                "arguments": {
                    "requirements": "以后续写章节时减少比喻，避免不是……而是……句式。",
                    "scope": "writing",
                },
            },
        )

        self.assertEqual(result["status"], "ok")
        skill = self.db.query(Skill).filter(Skill.project_id == self.project.id, Skill.is_builtin == False).first()  # noqa: E712
        self.assertIsNotNone(skill)
        self.assertIn("比喻", skill.description or skill.system_prompt)

    async def test_agent_can_export_project(self):
        self.db.add(Chapter(project_id=self.project.id, title="第一章", content="测试正文", word_count=4))
        self.db.commit()

        result = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "export_project", "arguments": {"scope": "chapters", "format": "txt"}},
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["format"], "txt")
        self.assertTrue(result["data"]["file_id"])

    async def test_agent_can_import_local_file_as_project(self):
        text = "\n".join([
            "第一章 初入陆家",
            "特昂糖睁开眼，看见院中的石狮子。",
            "",
            "第二章 吐纳",
            "陆老爷子教她吐纳，灵气第一次沿经脉运行。",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write(text)
            path = handle.name

        try:
            result = await execute_workspace_action(
                self.db,
                self.project.id,
                {
                    "tool": "import_file_as_project",
                    "arguments": {
                        "file_path": path,
                        "title": "穿越女娃，竟被病毒追着杀",
                    },
                },
            )
            self.db.commit()
        finally:
            os.remove(path)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["project"]["title"], "穿越女娃，竟被病毒追着杀")
        self.assertEqual(result["data"]["total"], 2)
        imported = self.db.query(Chapter).filter(Chapter.project_id == result["data"]["project"]["id"]).all()
        self.assertEqual(len(imported), 2)

    async def test_list_characters_excludes_merged_alias_placeholders(self):
        self.db.add(Character(project_id=self.project.id, name="Primary", role_type="protagonist"))
        self.db.add(Character(project_id=self.project.id, name="Alias Placeholder", role_type="merged_alias"))
        self.db.commit()

        result = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "list_characters", "arguments": {}},
        )

        self.assertEqual(result["status"], "ok")
        names = [item["name"] for item in result["data"]]
        self.assertEqual(names, ["Primary"])

        search_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "search_characters", "arguments": {"query": "Alias"}},
        )
        self.assertEqual(search_result["status"], "ok")
        self.assertEqual(search_result["data"], [])

    async def test_agent_can_list_and_restore_chapter_versions(self):
        create_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {
                "tool": "create_chapter",
                "arguments": {
                    "title": "第1章",
                    "content": "第一版正文",
                    "skip_style_repair": True,
                },
            },
        )
        self.assertEqual(create_result["status"], "ok")
        chapter_id = create_result["data"]["chapter_id"]

        update_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {
                "tool": "update_chapter",
                "arguments": {
                    "chapter_id": chapter_id,
                    "content": "第二版正文",
                    "skip_style_repair": True,
                },
            },
        )
        self.assertEqual(update_result["status"], "ok")
        self.assertEqual(update_result["data"]["current_version"], 2)

        versions = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "list_chapter_versions", "arguments": {"chapter_id": chapter_id}},
        )
        self.assertEqual(versions["status"], "ok")
        self.assertEqual(versions["data"]["total"], 2)
        self.assertEqual([item["version_number"] for item in versions["data"]["items"]], [2, 1])

        restore_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "restore_chapter_version", "arguments": {"chapter_id": chapter_id}},
        )
        self.assertEqual(restore_result["status"], "ok")
        self.assertEqual(restore_result["data"]["restored_from"]["version_number"], 1)

        chapter = self.db.query(Chapter).filter(Chapter.id == chapter_id).first()
        self.assertIsNotNone(chapter)
        self.assertEqual(chapter.content, "第一版正文")
        self.assertEqual(chapter.current_version, 3)

    async def test_update_preserves_legacy_chapter_before_first_snapshot(self):
        chapter = Chapter(
            project_id=self.project.id,
            title="legacy chapter",
            content="old content",
            word_count=2,
            current_version=1,
        )
        self.db.add(chapter)
        self.db.commit()
        self.db.refresh(chapter)

        update_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {
                "tool": "update_chapter",
                "arguments": {
                    "chapter_id": chapter.id,
                    "content": "new content",
                    "skip_style_repair": True,
                },
            },
        )

        self.assertEqual(update_result["status"], "ok")
        self.assertEqual(update_result["data"]["current_version"], 2)

        versions = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "list_chapter_versions", "arguments": {"chapter_id": chapter.id}},
        )
        self.assertEqual(versions["status"], "ok")
        self.assertEqual([item["version_number"] for item in versions["data"]["items"]], [2, 1])

        restore_result = await execute_workspace_action(
            self.db,
            self.project.id,
            {"tool": "restore_chapter_version", "arguments": {"chapter_id": chapter.id}},
        )
        self.assertEqual(restore_result["status"], "ok")
        self.assertEqual(restore_result["data"]["restored_from"]["version_number"], 1)

        restored = self.db.query(Chapter).filter(Chapter.id == chapter.id).first()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.content, "old content")
        self.assertEqual(restored.current_version, 3)


class WorkspaceSystemIntentTestCase(unittest.TestCase):
    """Plan Agent should recognize system-management intents."""

    def test_detect_scheduled_task_intent(self):
        intent = detect_intent("每天22点帮我搜索仙侠榜单并整理灵感")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "scheduled_task")
        self.assertEqual(intent["cron_expr"], "0 22 * * *")
        graph = build_plan_from_intent(intent)
        self.assertIsNotNone(graph)
        self.assertIn("create_scheduled_task", graph.steps)

    def test_detect_skill_intent(self):
        intent = detect_intent("创建技能：以后写作时禁用大量比喻")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "skill")
        graph = build_plan_from_intent(intent)
        self.assertIsNotNone(graph)
        self.assertIn("create_skill", graph.steps)

    def test_detect_export_intent(self):
        intent = detect_intent("导出全文为 docx")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "export")
        self.assertEqual(intent["format"], "docx")
        graph = build_plan_from_intent(intent)
        self.assertIsNotNone(graph)
        self.assertIn("export_project", graph.steps)

    def test_detect_cataloging_init_intent_uses_real_tools(self):
        intent = detect_intent("开始给当前作品建档")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "project_init")
        graph = build_plan_from_intent(intent)
        self.assertIsNotNone(graph)
        self.assertEqual(set(graph.steps.keys()), {"list_chapters", "start_cataloging_job"})

    def test_detect_deconstruct_intent(self):
        intent = detect_intent("帮我拆书分析当前作品")
        self.assertIsNotNone(intent)
        self.assertEqual(intent["intent_type"], "deconstruct")
        graph = build_plan_from_intent(intent)
        self.assertIsNotNone(graph)
        self.assertIn("start_deconstruct_job", graph.steps)

    def test_registry_exposes_system_tools_without_api_key_tools(self):
        names = set(registry.all_names())
        self.assertIn("create_scheduled_task", names)
        self.assertIn("create_skill", names)
        self.assertIn("create_project", names)
        self.assertIn("export_project", names)
        self.assertIn("preview_import_splits", names)
        self.assertIn("import_text_as_chapters", names)
        self.assertIn("import_file_as_chapters", names)
        self.assertIn("import_file_as_project", names)
        self.assertIn("start_cataloging_job", names)
        self.assertIn("list_cataloging_candidates", names)
        self.assertIn("list_chapter_versions", names)
        self.assertIn("restore_chapter_version", names)
        self.assertIn("diff_chapter_versions", names)
        self.assertIn("start_deconstruct_job", names)
        self.assertIn("get_today_writing_stats", names)
        self.assertIn("set_daily_word_goal", names)
        self.assertIn("list_duplicate_characters", names)
        self.assertIn("merge_duplicate_characters", names)
        self.assertNotIn("update_api_key", names)
        self.assertNotIn("create_api_key", names)
