"""Tests for Siming-managed local CLI agent worker contracts."""

import os
import asyncio
import sys
import tempfile
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pathlib import Path

from app.database.models import APIConfig, AgentRun, AgentRunEvent, Base, Chapter, Project
from app.services.external_agent.run_service import create_run
from app.services.cataloging.local_cli_agent import _task_text, _turn_stage
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.local_cli_agent_worker import _run_cli_process, start_local_cli_agent_worker, write_task_file
from app.services.workspace.registry import registry


class LocalCLIAgentWorkerTestCase(unittest.TestCase):
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
        project = Project(title="中文小说", description="测试")
        self.db.add(project)
        self.db.flush()
        return project

    def test_task_file_defines_read_mirror_and_mcp_write_boundary(self):
        project = self._project()
        task_file = write_task_file(
            self.db,
            project,
            run_id="run-1",
            user_request="给第一章建档",
            task_type="cataloging",
            provider="claude_cli",
        )

        text = task_file.read_text(encoding="utf-8")
        self.assertIn(f'project_id="{project.id}"', text)
        self.assertIn("The database is the only authoritative source.", text)
        self.assertIn("The project folder is a read-only mirror", text)
        self.assertIn("Every write/delete/update must use Siming MCP tools", text)
        self.assertIn('phase="merged"', text)
        self.assertIn("Do not call `save_external_cataloging_facts`", text)
        self.assertIn("Preserve the source novel language", text)

    def test_worker_requires_local_cli_config(self):
        project = self._project()
        result = start_local_cli_agent_worker(
            self.db,
            project.id,
            user_request="测试",
            task_type="general",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertIn("CLI", result["detail"])

    def test_registry_exposes_local_cli_agent_tool(self):
        tool = registry.get("start_local_cli_agent_run")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.tool_type, "scheduler")
        self.assertEqual(tool.estimated_cost, "local_cli")

    def test_start_worker_enables_opencode_warn_logs(self):
        project = self._project()
        self.db.add(APIConfig(
            provider="opencode_cli",
            provider_type="local_cli",
            api_key_encrypted="",
            default_model="opencode/deepseek-v4-flash-free",
            cli_command="opencode",
            cli_args='["run","--pure","{prompt}"]',
            is_global_default=True,
        ))
        self.db.commit()
        created_coroutines = []

        def capture_task(coroutine):
            created_coroutines.append(coroutine)

            class DummyTask:
                pass

            return DummyTask()

        with unittest.mock.patch(
            "app.services.local_cli_agent_worker.asyncio.create_task",
            side_effect=capture_task,
        ):
            result = start_local_cli_agent_worker(
                self.db,
                project.id,
                user_request="test",
                task_type="general",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(created_coroutines), 1)
        coroutine = created_coroutines[0]
        try:
            args = coroutine.cr_frame.f_locals["args"]
            self.assertEqual(args[:4], ["--print-logs", "--log-level", "WARN", "run"])
        finally:
            coroutine.close()

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
            "opencode_cli:opencode/deepseek-v4-flash-free",
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
            stage="merged",
        )

        self.assertIn(str(chapter_file), task)
        self.assertIn("include_content=false", task)
        self.assertIn("include_context_indexes=false", task)
        self.assertIn('phase="merged"', task)
        self.assertIn("save_external_cataloging_candidates", task)
        self.assertIn("Do not call `save_external_cataloging_facts`", task)
        self.assertIn("所有事实、候选和应用操作必须调用 Siming MCP 工具", task)
        self.assertIn("report_agent_progress", task)
        self.assertNotIn(chapter.content, task)
        self.assertEqual(_turn_stage(run, "auto"), "merged")

        run.status = "facts_saved"
        self.assertEqual(_turn_stage(run, "auto"), "candidates")
        run.status = "awaiting_confirmation"
        self.assertEqual(_turn_stage(run, "auto"), "apply")

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

        with unittest.mock.patch("app.services.local_cli_agent_worker.SessionLocal", self.Session):
            asyncio.run(_run_cli_process(
                run_id=run.id,
                project_id=project.id,
                provider="custom_cli",
                command=sys.executable,
                args=["-c", "print('HTTP 429 Too Many Requests: quota exceeded')"],
                stdin_text=None,
                cwd=self.tmp.name,
            ))

        self.db.expire_all()
        refreshed = self.db.query(AgentRun).filter(AgentRun.id == run.id).first()
        event = (
            self.db.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run.id, AgentRunEvent.status == "error")
            .order_by(AgentRunEvent.sequence.desc())
            .first()
        )
        self.assertEqual(refreshed.status, "failed")
        self.assertIn("额度/限额", refreshed.summary)
        self.assertIn("额度/限额", event.message)

    def test_worker_aborts_retrying_quota_process_before_timeout(self):
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
            "print('Free usage exceeded, subscribe to Go [retrying in 9h 28m attempt #1]', flush=True); "
            "time.sleep(5)"
        )

        started = time.monotonic()
        with unittest.mock.patch("app.services.local_cli_agent_worker.SessionLocal", self.Session):
            asyncio.run(_run_cli_process(
                run_id=run.id,
                project_id=project.id,
                provider="custom_cli",
                command=sys.executable,
                args=["-c", code],
                stdin_text=None,
                cwd=self.tmp.name,
            ))

        self.assertLess(time.monotonic() - started, 3)
        self.db.expire_all()
        refreshed = self.db.query(AgentRun).filter(AgentRun.id == run.id).first()
        event = (
            self.db.query(AgentRunEvent)
            .filter(AgentRunEvent.run_id == run.id, AgentRunEvent.status == "error")
            .order_by(AgentRunEvent.sequence.desc())
            .first()
        )
        self.assertEqual(refreshed.status, "failed")
        self.assertIn("Free usage exceeded", refreshed.summary)
        self.assertIn("Free usage exceeded", event.message)
