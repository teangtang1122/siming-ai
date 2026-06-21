"""State-machine tests for Moshu-managed local CLI cataloging."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base, CatalogingJob, Chapter, Project
from app.services.cataloging.local_cli_agent import _coordinate_cataloging
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.workspace.tools.cataloging import apply_pending_cataloging
from app.services.workspace.tools.external_cataloging import (
    get_next_external_cataloging_chapter,
    save_external_cataloging_candidates,
    save_external_cataloging_facts,
)


class LocalCLICatalogingAgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = os.environ.get("MOSHU_CONTENT_ROOT")
        os.environ["MOSHU_CONTENT_ROOT"] = self.tmp.name
        self.db_path = os.path.join(self.tmp.name, "cataloging-agent.db")
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            self.project = Project(title="本机 CLI 建档测试")
            db.add(self.project)
            db.flush()
            self.chapter = Chapter(
                project_id=self.project.id,
                title="第一章 开门",
                content="林舟推开旧门，看见门后站着另一个自己。",
            )
            db.add(self.chapter)
            db.add(APIConfig(
                provider="opencode_cli",
                provider_type="local_cli",
                api_key_encrypted="",
                default_model="opencode/deepseek-v4-flash-free",
                cli_command="opencode",
                is_global_default=True,
            ))
            db.commit()
            db.refresh(self.project)
            db.refresh(self.chapter)
            self.project_id = self.project.id
            self.chapter_id = self.chapter.id
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()
        if self.old_root is None:
            os.environ.pop("MOSHU_CONTENT_ROOT", None)
        else:
            os.environ["MOSHU_CONTENT_ROOT"] = self.old_root
        self.tmp.cleanup()

    async def _fake_cli_turn(self, *, job, run, agent_run_id, stage, **_kwargs):
        db = self.Session()
        try:
            if stage == "full":
                assigned = await get_next_external_cataloging_chapter(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "phase": "facts",
                        "include_content": False,
                        "include_prompt_pack": False,
                        "include_context_indexes": False,
                    },
                )
                self.assertIsNone(assigned["data"]["content"])
                await save_external_cataloging_facts(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "facts": [
                            {
                                "type": "chapter_overview",
                                "data": {"summary": "林舟推开旧门并看见另一个自己。"},
                            },
                        ],
                    },
                )
                await save_external_cataloging_candidates(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "candidates": [
                            {
                                "type": "summary",
                                "action": "create",
                                "summary": "林舟推开旧门并看见另一个自己。",
                            },
                            {
                                "type": "outline",
                                "action": "create",
                                "title": "第一章 开门",
                                "summary": "林舟在旧门后遭遇异常自我。",
                            },
                        ],
                    },
                )
                if job.execution_mode == "auto":
                    await apply_pending_cataloging(
                        db,
                        job.project_id,
                        {"job_id": job.id},
                    )
                    db.commit()
            elif stage == "apply":
                await apply_pending_cataloging(
                    db,
                    job.project_id,
                    {"job_id": job.id},
                )
                db.commit()
            return 0, f"agent run {agent_run_id} ok", ""
        finally:
            db.close()

    def _create_job(self, mode: str) -> str:
        db = self.Session()
        try:
            job = create_cataloging_job(
                db,
                self.project_id,
                mode,
                "opencode_cli:opencode/deepseek-v4-flash-free",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            return job.id
        finally:
            db.close()

    def test_auto_mode_processes_and_applies_the_chapter(self):
        job_id = self._create_job("auto")
        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch(
                "app.services.cataloging.local_cli_agent._run_cli_turn",
                side_effect=self._fake_cli_turn,
            ),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))

        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(job.completed_chapters, 1)
            self.assertIsNotNone(job.agent_run_id)
            self.assertEqual(job.chapter_runs[0].status, "completed")
            self.assertIsNotNone(job.chapter_runs[0].chapter.summary)
        finally:
            db.close()

    def test_manual_mode_stops_after_candidates_are_staged(self):
        job_id = self._create_job("manual")
        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch(
                "app.services.cataloging.local_cli_agent._run_cli_turn",
                side_effect=self._fake_cli_turn,
            ),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))

        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            self.assertEqual(job.status, "waiting_confirmation")
            self.assertEqual(job.chapter_runs[0].status, "awaiting_confirmation")
            self.assertGreater(len(job.chapter_runs[0].candidates), 0)
            self.assertIsNone(job.chapter_runs[0].chapter.summary)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
