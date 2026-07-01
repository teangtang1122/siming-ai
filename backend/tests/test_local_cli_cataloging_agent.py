"""State-machine tests for Siming-managed local CLI cataloging."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import APIConfig, Base, CatalogingFact, CatalogingJob, Chapter, Project
from app.services.cataloging.local_cli_agent import (
    _MAX_NO_SAVE_ATTEMPTS,
    _build_cataloging_cli_launch,
    _coordinate_cataloging,
    _task_prompt,
)
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.workspace.tools.cataloging import apply_pending_cataloging
from app.services.workspace.tools.external_cataloging import (
    get_next_external_cataloging_chapter,
    save_external_cataloging_candidates,
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
            if stage == "merged":
                assigned = await get_next_external_cataloging_chapter(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "phase": "merged",
                        "include_content": False,
                        "include_prompt_pack": False,
                        "include_context_indexes": False,
                    },
                )
                self.assertIsNone(assigned["data"]["content"])
                await save_external_cataloging_candidates(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "phase": "merged",
                        "candidates": [
                            {
                                "type": "outline",
                                "action": "create",
                                "title": "chapter outline",
                                "summary": "single-stage outline",
                            },
                            {
                                "type": "chapter_overview",
                                "data": {"summary": "林舟推开旧门并看见另一个自己。"},
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
            elif stage == "candidates":
                assigned = await get_next_external_cataloging_chapter(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "phase": "candidates",
                        "include_content": False,
                        "include_prompt_pack": False,
                        "include_context_indexes": False,
                    },
                )
                self.assertIsNone(assigned["data"]["content"])
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
            self.assertEqual(db.query(CatalogingFact).count(), 0)
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

    def test_opencode_turn_attaches_the_exact_chapter_task_file(self):
        from app.database.models import CatalogingChapterRun

        config = APIConfig(
            provider="opencode_cli",
            provider_type="local_cli",
            cli_args='["run","--pure","--format","json","{prompt}"]',
        )
        run = CatalogingChapterRun(
            id="chapter-run-7",
            chapter_id=self.chapter_id,
            chapter_order=6,
        )
        job = CatalogingJob(
            id="job-7",
            project_id=self.project_id,
        )
        chapter = Chapter(id=self.chapter_id, title="第七章 寿宴发难")
        with tempfile.TemporaryDirectory() as directory:
            task_file = __import__("pathlib").Path(directory) / "0007-merged.md"
            task_file.write_text("第七章唯一任务", encoding="utf-8")
            prompt = _task_prompt(task_file, job, run, chapter, "agent-run-7", "merged")
            launch = _build_cataloging_cli_launch(
                config=config,
                prompt=prompt,
                model="opencode/deepseek-v4-flash-free",
                task_file=task_file,
                project_folder=__import__("pathlib").Path(directory),
                run=run,
            )

        self.assertIn("chapter-run-7", prompt)
        self.assertIn(self.chapter_id, prompt)
        self.assertIn("--file", launch.args)
        self.assertEqual(launch.args[launch.args.index("--file") + 1], str(task_file))
        self.assertLess(launch.args.index("--file"), launch.args.index(prompt))
        self.assertIn("--dir", launch.args)
        self.assertLess(launch.args.index("--dir"), launch.args.index(prompt))
        self.assertIn("--title", launch.args)
        self.assertIn("0007", launch.args[launch.args.index("--title") + 1])

    def test_no_save_turn_is_retried_before_pausing_job(self):
        job_id = self._create_job("auto")
        attempts = 0
        stages = []

        async def flaky_cli_turn(**kwargs):
            nonlocal attempts
            attempts += 1
            stages.append(kwargs["stage"])
            if attempts == 1:
                return 0, "stale task binding", ""
            return await self._fake_cli_turn(**kwargs)

        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch(
                "app.services.cataloging.local_cli_agent._run_cli_turn",
                side_effect=flaky_cli_turn,
            ),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))

        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            self.assertEqual(attempts, 2)
            self.assertEqual(stages, ["merged", "merged"])
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(job.chapter_runs[0].status, "completed")
        finally:
            db.close()

    def test_no_save_turn_uses_direct_jsonl_fallback_before_pausing_job(self):
        job_id = self._create_job("auto")
        attempts = 0
        fallback_calls = 0

        async def stalled_cli_turn(**_kwargs):
            nonlocal attempts
            attempts += 1
            return 0, "finished without MCP writes", ""

        async def direct_fallback(db, *, job, run, stage, **_kwargs):
            nonlocal fallback_calls
            fallback_calls += 1
            self.assertEqual(stage, "merged")
            run.status = "completed"
            job.status = "completed"
            job.completed_chapters = 1
            job.current_chapter_id = None
            job.blocked_chapter_id = None
            job.error = None
            db.commit()
            return True, ""

        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch(
                "app.services.cataloging.local_cli_agent._run_cli_turn",
                side_effect=stalled_cli_turn,
            ),
            patch(
                "app.services.cataloging.local_cli_agent._run_direct_jsonl_cataloging_fallback",
                side_effect=direct_fallback,
            ),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))

        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            self.assertEqual(attempts, _MAX_NO_SAVE_ATTEMPTS)
            self.assertEqual(fallback_calls, 1)
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(job.chapter_runs[0].status, "completed")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
