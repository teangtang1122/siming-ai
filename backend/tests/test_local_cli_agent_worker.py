"""Tests for Moshu-managed local CLI agent worker contracts."""

import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Project
from app.services.local_cli_agent_worker import start_local_cli_agent_worker, write_task_file
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
        self.assertIn("Every write/delete/update must use Moshu MCP tools", text)
        self.assertIn("Facts stage may be parallel", text)
        self.assertIn("candidate/apply stage must be strictly sequential", text)
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
