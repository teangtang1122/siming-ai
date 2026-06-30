"""Tests for Siming 2.x folder-backed content store."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Chapter, Character, Project, WorldbuildingEntry
from app.services.content_store import migrate_projects_to_content_root, refresh_project_from_files, sync_project_to_files
from app.services.workspace.tools.project_files import (
    list_project_files,
    read_project_file,
    search_project_files,
    sync_project_files,
    write_project_file,
)
from app.services.workspace.tools.search import search_chapters


class ContentStoreFileSourceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = os.environ.get("MOSHU_CONTENT_ROOT")
        os.environ["MOSHU_CONTENT_ROOT"] = self.tmp.name
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        if self.old_root is None:
            os.environ.pop("MOSHU_CONTENT_ROOT", None)
        else:
            os.environ["MOSHU_CONTENT_ROOT"] = self.old_root
        self.tmp.cleanup()

    def _project(self) -> Project:
        project = Project(title="中文作品", description="测试")
        self.db.add(project)
        self.db.flush()
        return project

    def test_sync_and_refresh_chapter_markdown(self):
        project = self._project()
        chapter = Chapter(project_id=project.id, title="第一章 初醒", content="特昂糖睁开眼。", word_count=7)
        self.db.add(chapter)
        self.db.flush()

        sync_project_to_files(self.db, project.id)
        self.db.flush()

        path = os.path.join(project.folder_path, chapter.content_file_path)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("第一章 初醒", text)
        self.assertIn("特昂糖睁开眼。", text)

        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("特昂糖睁开眼。", "特昂糖看见石狮子。"))

        refresh_project_from_files(self.db, project.id)
        self.assertEqual(chapter.content.strip(), "特昂糖看见石狮子。")

    def test_project_file_tools_read_write_search(self):
        project = self._project()
        self.db.add(Character(project_id=project.id, name="特昂糖", background="陆家女儿"))
        self.db.add(WorldbuildingEntry(project_id=project.id, dimension="culture", title="陆家", content="修仙家族"))
        self.db.flush()
        sync_project_to_files(self.db, project.id)

        result = asyncio.run(write_project_file(
            self.db,
            project.id,
            {"path": "outbox/note.txt", "content": "青石村求救"},
        ))
        self.assertEqual(result["status"], "ok")

        listing = asyncio.run(list_project_files(self.db, project.id, {"path": "outbox"}))
        self.assertEqual(listing["status"], "ok")
        self.assertEqual(listing["data"]["items"][0]["path"], "outbox/note.txt")

        read = asyncio.run(read_project_file(self.db, project.id, {"path": "outbox/note.txt"}))
        self.assertIn("青石村", read["data"]["content"])

        search = asyncio.run(search_project_files(self.db, project.id, {"query": "青石村"}))
        self.assertEqual(search["status"], "ok")
        self.assertEqual(len(search["data"]["matches"]), 1)

    def test_canonical_project_dirs_are_read_only_mirrors(self):
        project = self._project()
        sync_project_to_files(self.db, project.id)

        blocked = asyncio.run(write_project_file(
            self.db,
            project.id,
            {"path": "chapters/manual.md", "content": "不能直接写章节镜像"},
        ))
        self.assertEqual(blocked["status"], "skipped")
        self.assertIn("只读镜像", blocked["detail"])

        allowed = asyncio.run(write_project_file(
            self.db,
            project.id,
            {"path": "outbox/manual.md", "content": "可以写入非规范目录"},
        ))
        self.assertEqual(allowed["status"], "ok")

    def test_normal_reads_do_not_import_file_mirror_edits(self):
        project = self._project()
        chapter = Chapter(project_id=project.id, title="第一章", content="数据库正文", word_count=5)
        self.db.add(chapter)
        self.db.flush()
        sync_project_to_files(self.db, project.id)
        self.db.flush()

        path = os.path.join(project.folder_path, chapter.content_file_path)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("数据库正文", "文件镜像改动"))

        result = asyncio.run(search_chapters(self.db, project.id, {"query": "第一章"}))
        self.assertEqual(result["status"], "ok")
        self.assertIn("数据库正文", result["data"][0]["content"])
        self.assertNotIn("文件镜像改动", result["data"][0]["content"])
        self.assertEqual(chapter.content, "数据库正文")

    def test_sync_project_files_requires_confirmation_for_file_import(self):
        project = self._project()
        chapter = Chapter(project_id=project.id, title="第一章", content="数据库正文", word_count=5)
        self.db.add(chapter)
        self.db.flush()
        sync_project_to_files(self.db, project.id)
        self.db.flush()

        path = os.path.join(project.folder_path, chapter.content_file_path)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("数据库正文", "显式导入正文"))

        default_sync = asyncio.run(sync_project_files(self.db, project.id, {}))
        self.assertEqual(default_sync["status"], "ok")
        self.assertEqual(default_sync["data"]["direction"], "db_to_files")
        self.assertEqual(chapter.content, "数据库正文")

        skipped = asyncio.run(sync_project_files(self.db, project.id, {"direction": "files_to_db"}))
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("confirm_import_from_files", skipped["detail"])
        self.assertEqual(chapter.content, "数据库正文")

        # Re-apply the external edit because the default db_to_files sync refreshed the mirror.
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("数据库正文", "显式导入正文"))

        imported = asyncio.run(sync_project_files(
            self.db,
            project.id,
            {"direction": "files_to_db", "confirm_import_from_files": True},
        ))
        self.assertEqual(imported["status"], "ok")
        self.assertEqual(chapter.content.strip(), "显式导入正文")

    def test_migrate_projects_to_new_content_root_refreshes_and_cleans_old_folder(self):
        project = self._project()
        chapter = Chapter(project_id=project.id, title="第一章", content="旧正文", word_count=3)
        self.db.add(chapter)
        self.db.flush()
        sync_project_to_files(self.db, project.id)
        self.db.flush()

        old_root = os.environ["MOSHU_CONTENT_ROOT"]
        old_folder = project.folder_path
        chapter_path = os.path.join(project.folder_path, chapter.content_file_path)
        with open(chapter_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(chapter_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("旧正文", "外部修改后的正文"))

        new_root = os.path.join(self.tmp.name, "new-root")
        os.environ["MOSHU_CONTENT_ROOT"] = new_root
        result = migrate_projects_to_content_root(self.db, new_root, previous_root=old_root, cleanup_old=True)
        self.db.flush()

        self.assertEqual(result["migrated_projects"], 1)
        self.assertEqual(result["cleaned_project_folders"], 1)
        self.assertFalse(os.path.exists(old_folder))
        Path(project.folder_path).resolve().relative_to(Path(new_root).resolve())
        self.assertEqual(chapter.content.strip(), "外部修改后的正文")
        self.assertTrue(os.path.exists(os.path.join(project.folder_path, chapter.content_file_path)))
