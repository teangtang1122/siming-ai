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
    CatalogingCandidate,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    OperationRun,
    Project,
    WorldbuildingEntry,
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
                default_model="opencode/big-pickle",
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
        from tests.test_cataloging_plan import plan_rows
        with self.Session() as db:
            if stage == "planning":
                await get_next_external_cataloging_chapter(db, job.project_id,
                    {"job_id": job.id, "include_content": False, "include_prompt_pack": False})
                rows = plan_rows()
                rows[1]["title"] = "第一章 开门"
                result = await save_external_cataloging_candidates(db, job.project_id,
                    {"job_id": job.id, "chapter_id": run.chapter_id, "candidates": rows, "finalize": True})
                self.assertTrue(result["data"]["candidate_set_complete"], result)
            elif stage == "apply":
                await apply_pending_cataloging(db, job.project_id, {"job_id": job.id})
            db.commit()
        return 0, "本章计划已完成", ""

    def _create_job(self, mode: str) -> str:
        db = self.Session()
        try:
            job = create_cataloging_job(
                db,
                self.project_id,
                mode,
                "opencode_cli:opencode/big-pickle",
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
            task_file = __import__("pathlib").Path(directory) / "0007-planning.md"
            task_file.write_text("第七章唯一任务", encoding="utf-8")
            prompt = _task_prompt(task_file, job, run, chapter, "agent-run-7", "planning")
            task_text = _task_text(
                job=job,
                run=run,
                agent_run_id="agent-run-7",
                provider=config.provider,
                project=self.project,
                project_folder=__import__("pathlib").Path(directory),
                chapter=chapter,
                chapter_file=task_file,
                stage="planning",
            )
            launch = _build_cataloging_cli_launch(
                config=config,
                prompt=prompt,
                model="opencode/big-pickle",
                task_file=task_file,
                project_folder=__import__("pathlib").Path(directory),
                run=run,
            )

        self.assertIn("chapter-run-7", prompt)
        self.assertIn(self.chapter_id, prompt)
        self.assertIn("narrative_review", task_text)
        self.assertIn("coverage_manifest", task_text)
        self.assertIn("原生 candidates 数组", task_text)
        for field in ("character_bindings", "worldbuilding_bindings", "scenes", "finalize", "items_or_assets_before"):
            self.assertIn(field, task_text)
        self.assertNotIn("save_external_cataloging_facts", task_text)
        self.assertEqual(launch.args[:4], ["--print-logs", "--log-level", "WARN", "run"])
        self.assertIn("--file", launch.args)
        self.assertEqual(launch.args[launch.args.index("--file") + 1], str(task_file))
        self.assertLess(launch.args.index("--file"), launch.args.index(prompt))
        self.assertIn("--dir", launch.args)
        self.assertLess(launch.args.index("--dir"), launch.args.index(prompt))
        self.assertIn("--title", launch.args)
        self.assertIn("0007", launch.args[launch.args.index("--title") + 1])


    def test_managed_cataloging_directly_supplies_fixed_task_tools(self):
        from app.mcp.server import handle_message

        job_id = self._create_job("auto")
        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter_by(id=job_id).one()
            job.model = "opencode_cli:opencode/author-selected"
            config = db.query(APIConfig).filter_by(provider="opencode_cli").one()
            config.cli_command = sys.executable
            config.default_model = "opencode/different-default"
            db.commit()
            calls = []

            async def step(**kwargs):
                env = kwargs["env"]
                surface = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                self.assertEqual(set(surface["mcp"]), {"siming_turn"})
                command = surface["mcp"]["siming_turn"]["command"]
                self.assertIn(self.project_id, command)
                self.assertIn("cataloging_worker", command)
                self.assertEqual(env["DATABASE_URL"], f"sqlite:///{self.db_path}")
                self.assertNotIn("OPENCODE_PERMISSION", env)
                self.assertEqual(surface["permission"]["bash"], "deny")
                self.assertEqual(surface["permission"]["edit"], "deny")
                self.assertEqual(kwargs["model"], "opencode/author-selected")
                state_file = kwargs["category_file"]
                listed = json.loads(handle_message(
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                    project_id=self.project_id, permission_pack="cataloging_worker",
                    tool_category_state_file=state_file,
                ))
                names = {tool["name"] for tool in listed["result"]["tools"]}
                calls.append(names)
                self.assertNotIn("set_tool_categories", names)
                self.assertEqual(len(names), 9)
                self.assertIn("read_cataloging_archive", names)
                self.assertIn("report_agent_plan", names)
                self.assertNotIn("delete_project", names)
                return 0, "real boundary test", ""

            with (
                patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
                patch("app.services.cataloging.local_cli_agent._execute_cataloging_cli_step", side_effect=step),
                patch("app.ai.local_cli_prompt.managed_mcp_environment", return_value={
                    "DATABASE_URL": f"sqlite:///{self.db_path}",
                    "SIMING_CONTENT_ROOT": self.tmp.name,
                    "SIMING_KEY_FILE": os.path.join(self.tmp.name, "test.key"),
                }),
                patch.dict(os.environ, {"OPENCODE_PERMISSION": '{"*":"allow"}'}),
            ):
                result = asyncio.run(_run_cli_turn(
                    job=job, run=job.chapter_runs[0], project=self.project,
                    chapter=self.chapter, config=config, agent_run_id=job.agent_run_id,
                    stage="planning",
                ))
            self.assertEqual(result[0], 0)
            self.assertEqual(len(calls), 1)
        finally:
            db.close()

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
            self.assertEqual(attempts, 3)
            self.assertEqual(stages, ["planning", "planning", "apply"])
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(job.chapter_runs[0].status, "completed")
        finally:
            db.close()

    def test_stale_chapter_is_rejected_before_starting_cli(self):
        job_id = self._create_job("auto")
        with self.Session() as db:
            job = db.get(CatalogingJob, job_id)
            run = job.chapter_runs[0]
            chapter = db.get(Chapter, run.chapter_id)
            run.chapter_version = chapter.current_version
            chapter.current_version = (chapter.current_version or 0) + 1
            db.commit()
        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch("app.services.cataloging.local_cli_agent._run_cli_turn") as turn,
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))
        turn.assert_not_called()
        with self.Session() as db:
            job = db.get(CatalogingJob, job_id)
            self.assertEqual(job.status, "paused_on_failure")
            self.assertIn("版本", job.error)


    def test_no_save_turn_pauses_without_a_non_mcp_fallback(self):
        job_id = self._create_job("auto")
        attempts = 0

        async def stalled_cli_turn(**_kwargs):
            nonlocal attempts
            attempts += 1
            return 0, "finished without MCP writes", ""

        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch(
                "app.services.cataloging.local_cli_agent._run_cli_turn",
                side_effect=stalled_cli_turn,
            ),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))

        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            self.assertEqual(attempts, _MAX_NO_SAVE_ATTEMPTS)
            self.assertEqual(job.status, "paused_on_failure")
            self.assertEqual(job.chapter_runs[0].status, "failed")
            self.assertIn("MCP", job.error)
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

            stable_metrics = {
                "alive": True,
                "process_count": 1,
                "cpu_seconds": 0.0,
                "read_bytes": 0,
                "write_bytes": 0,
                "rss_bytes": 1,
                "metrics_available": True,
            }
            with patch(
                "app.services.cataloging.local_cli_agent.SessionLocal", self.Session
            ), patch(
                "app.ai.local_cli_monitor.sample_cli_process_tree",
                return_value=stable_metrics,
            ):
                with self.assertRaisesRegex(RuntimeError, "确认卡住"):
                    asyncio.run(_run_cli_turn(
                        job=job,
                        run=run,
                        project=self.project,
                        chapter=self.chapter,
                        config=config,
                        agent_run_id="agent-run-without-events",
                        stage="planning",
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
                        stage="planning",
                    ))
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
                        stage="planning",
                    ))

            self.assertLess(time.monotonic() - started, 3)
        finally:
            db.close()


    def test_candidate_stall_preserves_checkpoint_and_does_not_auto_retry(self):
        from app.ai.local_cli_monitor import CLIStalledError
        from app.services.cataloging.local_cli_progress import CandidateProgressProbe

        job_id = self._create_job("auto")
        attempts = []

        async def stalled_turn(**kwargs):
            attempts.append(kwargs["stage"])
            if kwargs["stage"] == "removed_stage":
                return await self._fake_cli_turn(**kwargs)
            with self.Session() as db:
                job = db.get(CatalogingJob, job_id)
                run = job.chapter_runs[0]
                db.add(CatalogingCandidate(
                    job_id=job.id, chapter_run_id=run.id,
                    project_id=job.project_id, chapter_id=run.chapter_id,
                    item_type="worldbuilding_create", raw_payload='{"title":"保留候选"}',
                ))
                db.commit()
                probe = CandidateProgressProbe(category_file="", chapter_run_id=run.id,
                                               session_factory=self.Session)
                terminal, checkpoint = probe._checkpoint()
                self.assertFalse(terminal)
                self.assertEqual(len(checkpoint), 1)
                # Auto-apply intermediate states must not kill its transaction.
                for status in ("awaiting_confirmation", "applying"):
                    run.status = status
                    db.commit()
                    self.assertFalse(probe._checkpoint()[0])
                run.status = "extracting"
                db.commit()
            raise CLIStalledError("候选连续三次提交未产生有效进展：chapter_link 必须聚合为一条")

        with (
            patch("app.services.cataloging.local_cli_agent.SessionLocal", self.Session),
            patch("app.services.cataloging.local_cli_agent._run_cli_turn", side_effect=stalled_turn),
        ):
            asyncio.run(_coordinate_cataloging(job_id, "opencode_cli"))
        self.assertEqual(attempts, ["planning"])
        with self.Session() as db:
            job = db.get(CatalogingJob, job_id)
            self.assertEqual(job.status, "paused_on_failure")
            self.assertIn("chapter_link", job.error)
            self.assertEqual(db.query(CatalogingFact).filter_by(job_id=job_id).count(), 0)
            candidate = db.query(CatalogingCandidate).filter_by(job_id=job_id).one()
            self.assertEqual(candidate.status, "pending")
            self.assertEqual(json.loads(candidate.raw_payload), {"title": "保留候选"})


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
        assert permissions[f"siming_turn_{tool_name}"] == "allow"
    assert permissions["siming_turn_set_tool_categories"] == "allow"
    assert "siming_*" not in permissions
