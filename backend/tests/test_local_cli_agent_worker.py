"""Tests for managed CLI project, cataloging, writing, and outline paths."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AgentRun, AgentRunEvent, Base, Chapter, Project
from app.services.cataloging.local_cli_agent import _task_text, _turn_stage
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.external_agent.run_service import create_run
from app.services.local_cli_agent_worker import (
    _run_cli_process,
    _task_prompt,
    start_local_cli_agent_worker,
    write_task_file,
)
from app.services.workspace.registry import registry


class LocalCLIAgentWorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = os.environ.get("MOSHU_CONTENT_ROOT")
        os.environ["MOSHU_CONTENT_ROOT"] = self.tmp.name
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        if self.old_root is None:
            os.environ.pop("MOSHU_CONTENT_ROOT", None)
        else:
            os.environ["MOSHU_CONTENT_ROOT"] = self.old_root
        self.tmp.cleanup()

    def _project(self) -> Project:
        project = Project(title="中文小说", description="测试")
        self.db.add(project)
        self.db.flush()
        return project

    def test_task_file_keeps_mirror_read_only_and_database_authoritative(self):
        project = self._project()
        task = write_task_file(
            self.db,
            project,
            run_id="run-general-1",
            user_request="整理设定",
            task_type="general",
            provider="opencode_cli",
        )
        text = task.read_text(encoding="utf-8")
        self.assertIn("The database is the only authoritative source", text)
        self.assertIn("Do not edit, delete, rename, or create files", text)
        self.assertIn("General Project Work", text)
        self.assertNotIn("Required Workflow: Writing", text)
        self.assertNotIn("create_chapter", text)
        self.assertNotIn("update_chapter", text)

    def test_managed_cli_prompt_is_a_single_line_task_pointer(self):
        prompt = _task_prompt(Path(self.tmp.name) / "task.md")
        self.assertNotIn("\n", prompt)
        self.assertIn("task.md", prompt)

    def test_managed_worker_accepts_writing_mode_before_project_lookup(self):
        result = start_local_cli_agent_worker(
            self.db,
            "missing-project",
            user_request="写第一章",
            task_type="writing",
            provider="opencode_cli",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["detail"], "Project not found")

    def test_registry_exposes_draft_task_types_and_target_arguments(self):
        tool = registry.get("start_local_cli_agent_run")
        self.assertIsNotNone(tool)
        task_type = tool.input_schema["task_type"]
        self.assertEqual(
            task_type["enum"],
            ["general", "cataloging", "writing", "outline_planning"],
        )
        self.assertIn("outline_node_id", tool.input_schema)
        self.assertIn("parent_id", tool.input_schema)
        self.assertIn("insert_after_id", tool.input_schema)

    def test_cataloging_task_reads_chapter_file_and_writes_through_mcp(self):
        project = self._project()
        chapter = Chapter(
            project_id=project.id,
            title="第一章 旧门",
            content="这段正文不应被复制进 CLI 任务文件。",
        )
        self.db.add(chapter)
        self.db.commit()
        job = create_cataloging_job(
            self.db,
            project.id,
            "auto",
            "opencode_cli:opencode/big-pickle",
            [chapter.id],
            execution_backend="local_cli_agent",
        )
        run = job.chapter_runs[0]
        project_folder = Path(self.tmp.name) / "project"
        chapter_file = project_folder / "chapters" / "0001.md"

        task = _task_text(
            job=job,
            run=run,
            agent_run_id="agent-run-1",
            provider="opencode_cli",
            project=project,
            project_folder=project_folder,
            chapter=chapter,
            chapter_file=chapter_file,
            stage="facts",
        )

        self.assertIn(str(chapter_file), task)
        self.assertIn('phase="facts"', task)
        self.assertIn("save_external_cataloging_facts", task)
        self.assertIn("`facts` 必须直接传原生 JSON 数组", task)
        self.assertNotIn(chapter.content, task)
        self.assertEqual(_turn_stage(run, "auto"), "facts")

    def test_worker_marks_run_failed_when_cli_reports_quota(self):
        project = self._project()
        run = create_run(
            self.db,
            project.id,
            source="internal_cli",
            client_name="custom_cli",
            title="quota test",
        )
        self.db.commit()

        with patch("app.services.local_cli_agent_worker.SessionLocal", self.Session):
            asyncio.run(_run_cli_process(
                run_id=run.id,
                provider="custom_cli",
                command=sys.executable,
                args=["-c", "print('HTTP 429 Too Many Requests: quota exceeded')"],
                stdin_text=None,
                cwd=self.tmp.name,
            ))

        self.db.expire_all()
        refreshed = self.db.get(AgentRun, run.id)
        event = (
            self.db.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run.id, AgentRunEvent.status == "error")
            .order_by(AgentRunEvent.sequence.desc())
            .first()
        )
        self.assertEqual(refreshed.status, "failed")
        self.assertIn("额度/限额", refreshed.summary)
        self.assertIn("额度/限额", event.message)

    def test_worker_stops_retrying_quota_process_without_waiting_for_child_timeout(self):
        project = self._project()
        run = create_run(
            self.db,
            project.id,
            source="internal_cli",
            client_name="custom_cli",
            title="retrying quota test",
        )
        self.db.commit()
        code = (
            "import time; "
            "print('Free usage exceeded, subscribe to Go [retrying in 9h]', flush=True); "
            "time.sleep(5)"
        )

        started = time.monotonic()
        with patch("app.services.local_cli_agent_worker.SessionLocal", self.Session):
            asyncio.run(_run_cli_process(
                run_id=run.id,
                provider="custom_cli",
                command=sys.executable,
                args=["-c", code],
                stdin_text=None,
                cwd=self.tmp.name,
            ))

        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
