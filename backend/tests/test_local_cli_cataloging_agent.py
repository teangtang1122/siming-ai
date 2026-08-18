"""State-machine tests for Siming-managed local CLI cataloging."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    APIConfig,
    Base,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    OperationRun,
    Project,
)
from app.services.cataloging.local_cli_agent import (
    _build_cataloging_cli_launch,
    _coordinate_cataloging,
    _run_cli_turn,
    _task_prompt,
    _task_text,
)
from app.services.cataloging.local_cli_result import _MAX_NO_SAVE_ATTEMPTS
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
                                "type": "chapter_summary",
                                "summary_text": "林舟推开旧门并看见另一个自己。",
                                "coverage_manifest": {
                                    "scene_count": 1,
                                    "characters": ["林舟"],
                                    "worldbuilding": [],
                                    "relationships": [],
                                    "character_profiles": ["林舟"],
                                },
                                "narrative_state": {
                                    "events": [{"description": "林舟推开旧门。"}],
                                    "timeline_events": [],
                                    "foreshadowing_planted": [],
                                    "foreshadowing_resolved": [],
                                    "storyline_progress": [],
                                    "new_storylines": [],
                                    "reader_known_facts": [],
                                    "character_known_facts": [],
                                    "unresolved_actions": [],
                                    "character_actions": [],
                                    "relationship_changes": [],
                                },
                                "narrative_review": {"source": "provided", "findings": []},
                            },
                            {
                                "type": "outline_create",
                                "node_type": "chapter",
                                "title": "第一章 开门",
                                "summary": "林舟在旧门后遭遇异常自我。",
                            },
                            {
                                "type": "character_create",
                                "name": "林舟",
                                "role_type": "protagonist",
                                "personality": "谨慎而好奇。",
                                "background": "推开旧门后看见另一个自己的旅人。",
                            },
                            {
                                "type": "character_state_update",
                                "name": "林舟",
                                "appearance": "沿用本章描写",
                                "age": "未明确",
                                "current_location": "旧门前",
                            },
                            {
                                "type": "chapter_link",
                                "character_names": ["林舟"],
                                "description": "本章出场",
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
                                "type": "chapter_summary",
                                "summary_text": "林舟推开旧门并看见另一个自己。",
                                "coverage_manifest": {
                                    "scene_count": 1,
                                    "characters": ["林舟"],
                                    "worldbuilding": [],
                                    "relationships": [],
                                    "character_profiles": ["林舟"],
                                },
                                "narrative_state": {"events": [{"description": "林舟推开旧门。"}]},
                                "narrative_review": {"source": "provided", "findings": []},
                            },
                            {
                                "type": "outline",
                                "action": "create",
                                "title": "第一章 开门",
                                "summary": "林舟在旧门后遭遇异常自我。",
                            },
                            {
                                "type": "character_create",
                                "name": "林舟",
                                "role_type": "protagonist",
                                "personality": "谨慎而好奇。",
                                "background": "推开旧门后看见另一个自己的旅人。",
                            },
                            {
                                "type": "character_state_update",
                                "name": "林舟",
                                "appearance": "沿用本章描写",
                                "age": "未明确",
                                "current_location": "旧门前",
                            },
                            {
                                "type": "chapter_link",
                                "character_names": ["林舟"],
                                "description": "本章出场",
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
            operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).one()
            self.assertEqual(operation.status, "completed")
            self.assertEqual(operation.progress_current, 1)
            self.assertEqual((operation.result_json or {}).get("outcome"), "completed_with_tools")
            self.assertEqual(
                db.query(CatalogingFact)
                .filter(CatalogingFact.fact_type == "chapter_overview")
                .count(),
                0,
            )
            self.assertGreater(db.query(CatalogingFact).count(), 0)
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
            self.assertEqual(job.status, "waiting_confirmation", job.error)
            self.assertEqual(job.chapter_runs[0].status, "awaiting_confirmation")
            self.assertGreater(len(job.chapter_runs[0].candidates), 0)
            self.assertIsNone(job.chapter_runs[0].chapter.summary)
            operation = db.query(OperationRun).filter(OperationRun.id == job.operation_id).one()
            self.assertEqual(operation.status, "waiting_user")
            self.assertEqual((operation.attention_json or {}).get("kind"), "confirmation")
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
            task_text = _task_text(
                job=job,
                run=run,
                agent_run_id="agent-run-7",
                provider=config.provider,
                project=self.project,
                project_folder=__import__("pathlib").Path(directory),
                chapter=chapter,
                chapter_file=task_file,
                stage="merged",
            )
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
        self.assertIn("narrative_review", task_text)
        self.assertIn("coverage_manifest", task_text)
        self.assertIn("auto_applied=true", task_text)
        self.assertIn("禁止再次 save/apply", task_text)
        self.assertIn("resolves_item_id", task_text)
        self.assertIn("不得按标题猜测关闭", task_text)
        self.assertEqual(launch.args[:4], ["--print-logs", "--log-level", "WARN", "run"])
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
                "app.services.cataloging.local_cli_result._run_direct_jsonl_cataloging_fallback",
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

    def test_cli_turn_aborts_when_agent_stops_reporting_progress(self):
        old_env = {
            name: os.environ.get(name)
            for name in [
                "SIMING_CATALOGING_CLI_POLL_SECONDS",
                "SIMING_CLI_SUSPECTED_STALL_SECONDS",
                "SIMING_CLI_STALLED_SECONDS",
            ]
        }
        os.environ["SIMING_CATALOGING_CLI_POLL_SECONDS"] = "0.05"
        os.environ["SIMING_CLI_SUSPECTED_STALL_SECONDS"] = "0.1"
        os.environ["SIMING_CLI_STALLED_SECONDS"] = "0.2"
        db = self.Session()
        try:
            job = create_cataloging_job(
                db,
                self.project_id,
                "auto",
                "custom_cli:custom-cli",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            run = job.chapter_runs[0]
            db.commit()
            config = APIConfig(
                provider="custom_cli",
                provider_type="local_cli",
                cli_command=sys.executable,
                cli_args=json.dumps(["-c", "import time; time.sleep(2)"]),
                default_model="custom-cli",
            )

            with patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session):
                with self.assertRaisesRegex(RuntimeError, "确认卡住"):
                    asyncio.run(_run_cli_turn(
                        job=job,
                        run=run,
                        project=self.project,
                        chapter=self.chapter,
                        config=config,
                        agent_run_id="agent-run-without-events",
                        stage="merged",
                    ))
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            db.close()

    def test_cli_turn_reports_provider_quota_as_terminal_error(self):
        db = self.Session()
        try:
            job = create_cataloging_job(
                db,
                self.project_id,
                "auto",
                "custom_cli:custom-cli",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            run = job.chapter_runs[0]
            db.commit()
            config = APIConfig(
                provider="custom_cli",
                provider_type="local_cli",
                cli_command=sys.executable,
                cli_args=json.dumps(["-c", "print('Error: quota exceeded for provider')"]),
                default_model="custom-cli",
            )

            with patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session):
                with self.assertRaisesRegex(RuntimeError, "额度/限额"):
                    asyncio.run(_run_cli_turn(
                        job=job,
                        run=run,
                        project=self.project,
                        chapter=self.chapter,
                        config=config,
                        agent_run_id="agent-run-quota",
                        stage="merged",
                    ))
        finally:
            db.close()

    def test_managed_auto_save_applies_complete_candidates_transactionally(self):
        job_id = self._create_job("auto")
        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).one()
            run = job.chapter_runs[0]
            env = {
                "SIMING_MANAGED_AGENT_KIND": "cataloging",
                "SIMING_MANAGED_CATALOGING_PROJECT_ID": self.project_id,
                "SIMING_MANAGED_CATALOGING_JOB_ID": job_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_ID": self.chapter_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
                "SIMING_MANAGED_CATALOGING_STAGE": "merged",
            }
            with patch.dict(os.environ, env, clear=False):
                assigned = asyncio.run(get_next_external_cataloging_chapter(
                    db,
                    self.project_id,
                    {"job_id": job_id, "phase": "merged"},
                ))
                self.assertEqual(assigned["status"], "ok")
                partial = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "phase": "merged",
                        "candidates": [
                            {
                                "type": "chapter_summary",
                                "summary_text": "林舟推开旧门并看见另一个自己。",
                                "coverage_manifest": {
                                    "scene_count": 1,
                                    "characters": ["林舟"],
                                    "worldbuilding": [],
                                    "relationships": [],
                                    "character_profiles": ["林舟"],
                                },
                                "narrative_state": {"events": [{"description": "林舟推开旧门。"}]},
                                "narrative_review": {"source": "provided", "findings": []},
                            },
                            {
                                "type": "outline_create",
                                "node_type": "chapter",
                                "title": "第一章 开门",
                                "summary": "林舟在旧门后遭遇异常自我。",
                            },
                        ],
                    },
                ))
                self.assertFalse(partial["data"]["candidate_set_complete"])
                self.assertFalse(partial["data"]["auto_applied"])
                self.assertIn(
                    "character_state_update for declared characters (0/1)",
                    partial["data"]["missing_required_items"],
                )
                self.assertEqual(partial["data"]["chapter_run_status"], "in_progress")

                saved = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "phase": "merged",
                        "candidates": [
                            {
                                "type": "character_create",
                                "name": "林舟",
                                "role_type": "protagonist",
                                "personality": "谨慎而好奇。",
                                "background": "推开旧门后看见另一个自己的旅人。",
                            },
                            {
                                "type": "character_state_update",
                                "name": "林舟",
                                "appearance": "沿用本章描写",
                                "age": "未明确",
                                "current_location": "旧门前",
                            },
                            {
                                "type": "chapter_link",
                                "character_names": ["林舟"],
                                "description": "本章出场",
                            },
                        ],
                    },
                ))

            self.assertEqual(saved["status"], "ok", saved)
            self.assertTrue(saved["data"]["candidate_set_complete"])
            self.assertTrue(saved["data"]["auto_applied"])
            self.assertEqual(saved["data"]["next_tool"], "verify_external_cataloging_progress")
            self.assertEqual(saved["data"]["chapter_run_status"], "completed")
            db.refresh(job)
            db.refresh(run)
            self.assertEqual(job.status, "completed")
            self.assertEqual(run.status, "completed")
            self.assertIsNotNone(run.chapter.summary)
        finally:
            db.close()

    def test_cli_turn_aborts_retrying_quota_process_before_idle_timeout(self):
        db = self.Session()
        try:
            job = create_cataloging_job(
                db,
                self.project_id,
                "auto",
                "custom_cli:custom-cli",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            run = job.chapter_runs[0]
            db.commit()
            code = (
                "import time; "
                "print('Free usage exceeded, subscribe to Go [retrying in 9h 28m attempt #1]', flush=True); "
                "time.sleep(5)"
            )
            config = APIConfig(
                provider="custom_cli",
                provider_type="local_cli",
                cli_command=sys.executable,
                cli_args=json.dumps(["-c", code]),
                default_model="custom-cli",
            )

            started = time.monotonic()
            with patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session):
                with self.assertRaisesRegex(RuntimeError, "Free usage exceeded"):
                    asyncio.run(_run_cli_turn(
                        job=job,
                        run=run,
                        project=self.project,
                        chapter=self.chapter,
                        config=config,
                        agent_run_id="agent-run-quota-retry",
                        stage="merged",
                    ))

            self.assertLess(time.monotonic() - started, 3)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

def test_opencode_cataloging_permission_env_is_read_only_except_cataloging_mcp():
    from app.services.cataloging.local_cli_mcp import opencode_cataloging_permission_env
    from app.services.external_agent.mcp_preflight import CATALOGING_MCP_TOOL_NAMES

    permissions = json.loads(opencode_cataloging_permission_env())
    assert permissions["edit"] == "deny"
    assert permissions["bash"] == "deny"
    assert permissions["external_directory"] == "deny"
    assert permissions["read"]["*"] == "allow"
    for tool_name in CATALOGING_MCP_TOOL_NAMES:
        assert permissions[f"siming_{tool_name}"] == "allow"
