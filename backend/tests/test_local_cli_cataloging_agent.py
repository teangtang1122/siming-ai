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
        db = self.Session()
        try:
            if stage == "facts":
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
                                "fact_type": "chapter_overview",
                                "evidence": "林舟推开旧门，看见门后站着另一个自己。",
                                "payload": {
                                    "summary": "林舟推开旧门并看见另一个自己。",
                                    "key_events": ["推开旧门", "遇见另一个自己"],
                                    "cataloging_characters": ["林舟"],
                                    "anonymous_participants": [],
                                    "cataloging_worldbuilding_titles": [],
                                    "incidental_worldbuilding_mentions": [],
                                },
                            },
                            {
                                "fact_type": "character_fact",
                                "evidence": "林舟推开旧门",
                                "payload": {
                                    "primary_name": "林舟",
                                    "archive_identity": "stable_character",
                                    "stable_profile_change": True,
                                    "actions": ["推开旧门"],
                                },
                            },
                        ],
                    },
                )
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
                first_batch = [
                    {
                        "type": "chapter_summary",
                        "summary_text": (
                            "林舟谨慎地推开旧门，在门后看见另一个自己；这场异常相遇"
                            "打破了他的既有判断，也迫使他决定继续核查旧门的来源与风险。"
                        ),
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
                ]
                second_batch = [
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
                        "life_status": "alive",
                        "current_location": "旧门前",
                    },
                    {
                        "type": "chapter_link",
                        "character_names": ["林舟"],
                        "description": "本章出场",
                    },
                ]
                await save_external_cataloging_candidates(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "candidates": first_batch,
                    },
                )
                await save_external_cataloging_candidates(
                    db,
                    job.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": run.chapter_id,
                        "candidates": second_batch,
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
                1,
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
            task_file = __import__("pathlib").Path(directory) / "0007-candidates.md"
            task_file.write_text("第七章唯一任务", encoding="utf-8")
            prompt = _task_prompt(task_file, job, run, chapter, "agent-run-7", "candidates")
            task_text = _task_text(
                job=job,
                run=run,
                agent_run_id="agent-run-7",
                provider=config.provider,
                project=self.project,
                project_folder=__import__("pathlib").Path(directory),
                chapter=chapter,
                chapter_file=task_file,
                stage="candidates",
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
        self.assertIn("`candidates` 必须是原生 JSON 数组", task_text)
        self.assertIn("每次调用最多 3 个候选", task_text)
        self.assertIn("首次调用必须恰好 2 个", task_text)
        self.assertIn("全章只保存一条聚合 `chapter_link`", task_text)
        self.assertIn("`栏目负责人`、`综合科记录人` 这类未具名岗位不是角色卡", task_text)
        self.assertIn("chapter_overview.payload.scenes", task_text)
        self.assertIn("auto_applied=true", task_text)
        self.assertIn("禁止再次 save/apply", task_text)
        self.assertIn("resolves_item_id", task_text)
        self.assertIn("不得按标题猜测关闭", task_text)
        self.assertIn("未明示实时地点时省略 current_location", task_text)
        self.assertIn("items_or_assets 是整字段替换", task_text)
        self.assertIn("items_or_assets_before", task_text)
        self.assertIn("同场其他人物经手的物件", task_text)
        self.assertIn("同一有向角色对只能选择一个当前 relationship_type", task_text)
        self.assertIn("每个角色只出现一次", task_text)
        self.assertIn("chapter_link_mode=\"replace\"", task_text)
        self.assertIn("source_fact_titles", task_text)
        self.assertEqual(launch.args[:4], ["--print-logs", "--log-level", "WARN", "run"])
        self.assertIn("--file", launch.args)
        self.assertEqual(launch.args[launch.args.index("--file") + 1], str(task_file))
        self.assertLess(launch.args.index("--file"), launch.args.index(prompt))
        self.assertIn("--dir", launch.args)
        self.assertLess(launch.args.index("--dir"), launch.args.index(prompt))
        self.assertIn("--title", launch.args)
        self.assertIn("0007", launch.args[launch.args.index("--title") + 1])

    def test_managed_candidate_boundary_rejects_large_batches_and_scene_drift(self):
        job_id = self._create_job("auto")
        db = self.Session()
        try:
            job = db.query(CatalogingJob).filter_by(id=job_id).one()
            run = job.chapter_runs[0]
            run.status = "facts_saved"
            job.status = "running"
            db.add(CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=self.project_id,
                chapter_id=self.chapter_id,
                fact_type="chapter_overview",
                raw_payload=json.dumps({
                    "summary": "事实摘要",
                    "scenes": [{"scene_number": 1}, {"scene_number": 2}],
                }, ensure_ascii=False),
                status="active",
            ))
            db.commit()
            summary = {
                "type": "chapter_summary",
                "summary_text": (
                    "林舟谨慎地推开旧门，在门后看见另一个自己；这场异常相遇"
                    "打破了他的既有判断，也迫使他决定继续核查旧门的来源与风险。"
                ),
                "coverage_manifest": {
                    "scene_count": 2,
                    "characters": [],
                    "worldbuilding": [],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {"events": []},
                "narrative_review": {"source": "provided", "findings": []},
            }
            outline = {
                "type": "outline_create",
                "node_type": "chapter",
                "title": self.chapter.title,
                "summary": "林舟在旧门后遭遇异常自我。",
            }
            env = {
                "SIMING_MANAGED_AGENT_KIND": "cataloging",
                "SIMING_MANAGED_CATALOGING_PROJECT_ID": self.project_id,
                "SIMING_MANAGED_CATALOGING_JOB_ID": job_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_ID": self.chapter_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
                "SIMING_MANAGED_CATALOGING_STAGE": "candidates",
            }
            with patch.dict(os.environ, env, clear=False):
                oversized = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            summary,
                            outline,
                            {"type": "outline_create", "node_type": "section", "title": "场景一"},
                            {"type": "chapter_link", "description": "聚合关联"},
                        ],
                    },
                ))
                self.assertEqual(oversized["status"], "skipped")
                self.assertTrue(any(
                    "at most 3" in error
                    for error in oversized["data"]["validation_errors"]
                ))
                self.assertEqual(
                    db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).count(),
                    0,
                )

                wrong_scene_summary = {
                    **summary,
                    "coverage_manifest": {
                        **summary["coverage_manifest"],
                        "scene_count": 3,
                    },
                }
                drift = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "candidates": [wrong_scene_summary, outline],
                    },
                ))
                self.assertEqual(drift["status"], "skipped")
                self.assertTrue(any(
                    "facts=2, manifest=3" in error
                    for error in drift["data"]["validation_errors"]
                ))
                self.assertEqual(
                    db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).count(),
                    0,
                )
        finally:
            db.close()

    def test_managed_cataloging_uses_scoped_mcp_and_model_selected_categories(self):
        from app.ai.local_cli_monitor import CLITurnTerminal
        from app.mcp.server import handle_message
        from app.services.tool_category_state import read_tool_category_state, replace_tool_categories

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
                if len(calls) == 1:
                    self.assertEqual(names, {"set_tool_categories"})
                    replace_tool_categories(state_file, ["cataloging", "agent_runtime"])
                    self.assertEqual(read_tool_category_state(state_file)["active_categories"], [])
                    raise CLITurnTerminal("set_tool_categories:1", stdout="category receipt", stderr="")
                self.assertIn("save_external_cataloging_facts", names)
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
                    stage="facts",
                ))
            self.assertEqual(result[0], 0)
            self.assertEqual(len(calls), 2)
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
            self.assertEqual(stages, ["facts", "facts", "candidates"])
            self.assertEqual(job.status, "completed", job.error)
            self.assertEqual(job.chapter_runs[0].status, "completed")
        finally:
            db.close()

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
                        stage="facts",
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
                        stage="facts",
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
                "SIMING_MANAGED_CATALOGING_STAGE": "facts",
            }
            with patch.dict(os.environ, env, clear=False):
                assigned = asyncio.run(get_next_external_cataloging_chapter(
                    db,
                    self.project_id,
                    {"job_id": job_id, "phase": "facts"},
                ))
                self.assertEqual(assigned["status"], "ok")
                facts_saved = asyncio.run(save_external_cataloging_facts(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "facts": [
                            {
                                "fact_type": "chapter_overview",
                                "evidence": "林舟推开旧门，看见门后站着另一个自己。",
                                "payload": {
                                    "summary": "林舟推开旧门并看见另一个自己。",
                                    "key_events": ["推开旧门", "遇见另一个自己"],
                                    "cataloging_characters": ["林舟"],
                                    "anonymous_participants": [],
                                    "cataloging_worldbuilding_titles": [],
                                    "incidental_worldbuilding_mentions": [],
                                },
                            },
                            {
                                "fact_type": "character_fact",
                                "evidence": "林舟推开旧门。",
                                "payload": {
                                    "primary_name": "林舟",
                                    "archive_identity": "stable_character",
                                    "stable_profile_change": True,
                                },
                            },
                        ],
                    },
                ))
                self.assertEqual(facts_saved["status"], "ok")
                os.environ["SIMING_MANAGED_CATALOGING_STAGE"] = "candidates"
                assigned = asyncio.run(get_next_external_cataloging_chapter(
                    db,
                    self.project_id,
                    {"job_id": job_id, "phase": "candidates"},
                ))
                self.assertEqual(assigned["status"], "ok")
                partial = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            {
                                "type": "chapter_summary",
                                "summary_text": (
                                    "林舟谨慎地推开旧门，在门后看见另一个自己；这场异常相遇"
                                    "打破了他的既有判断，也迫使他决定继续核查旧门的来源与风险。"
                                ),
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
                self.assertIn("missing:", partial["detail"])
                self.assertEqual(partial["data"]["chapter_run_status"], "facts_saved")
                db.refresh(job)
                self.assertEqual(job.status, "running")
                self.assertIsNone(job.blocked_chapter_id)

                amended = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job_id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_summary",
                            "summary_text": (
                                "林舟谨慎地推开旧门，在门后看见另一个自己；这场异常相遇"
                                "打破了他的既有判断，也迫使他决定继续核查旧门的来源与风险。"
                            ),
                            "coverage_manifest": {
                                "scene_count": 1,
                                "characters": ["林舟"],
                                "worldbuilding": [],
                                "relationships": [],
                                "character_profiles": ["林舟"],
                            },
                            "narrative_review": {
                                "source": "provided",
                                "findings": [],
                                "evidence": "已按原文复核人物与场景覆盖。",
                            },
                        }],
                    },
                ))
                self.assertEqual(amended["status"], "ok", amended)
                self.assertEqual(amended["data"]["candidates_saved"], 1)
                self.assertEqual(amended["data"]["candidates_total"], 2)
                self.assertFalse(amended["data"]["candidate_set_complete"])

                observed_pre_apply_state: dict[str, object] = {}

                async def observe_committed_pre_apply_state(
                    apply_db, apply_project_id, apply_args,
                ):
                    probe = self.Session()
                    try:
                        persisted_job = (
                            probe.query(CatalogingJob)
                            .filter(CatalogingJob.id == job_id)
                            .one()
                        )
                        persisted_run = persisted_job.chapter_runs[0]
                        observed_pre_apply_state.update({
                            "job_status": persisted_job.status,
                            "blocked_chapter_id": persisted_job.blocked_chapter_id,
                            "run_status": persisted_run.status,
                        })
                    finally:
                        probe.close()
                    return await apply_pending_cataloging(
                        apply_db,
                        apply_project_id,
                        apply_args,
                    )

                with patch(
                    "app.services.workspace.tools.cataloging.apply_pending_cataloging",
                    new=observe_committed_pre_apply_state,
                ):
                    saved = asyncio.run(save_external_cataloging_candidates(
                        db,
                        self.project_id,
                        {
                            "job_id": job_id,
                            "chapter_id": self.chapter_id,
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
                                    "life_status": "alive",
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
            self.assertEqual(observed_pre_apply_state["job_status"], "running")
            self.assertIsNone(observed_pre_apply_state["blocked_chapter_id"])
            self.assertEqual(
                observed_pre_apply_state["run_status"],
                "awaiting_confirmation",
            )
            db.refresh(job)
            db.refresh(run)
            self.assertEqual(job.status, "completed")
            self.assertEqual(run.status, "completed")
            self.assertIsNotNone(run.chapter.summary)
        finally:
            db.close()

    def test_managed_chapter_link_can_be_amended_without_creating_a_duplicate(self):
        db = self.Session()
        try:
            chapter = db.query(Chapter).filter(Chapter.id == self.chapter_id).one()
            chapter.content = "林舟推开旧门，看见门后空间里站着另一个自己。"
            old_door = WorldbuildingEntry(
                project_id=self.project_id,
                dimension="geography",
                title="旧门",
                content="一扇来历待查的旧门。",
                status="active",
            )
            behind = WorldbuildingEntry(
                project_id=self.project_id,
                dimension="geography",
                title="门后空间",
                content="旧门之后出现的异常空间。",
                status="active",
            )
            db.add_all([old_door, behind])
            db.commit()

            job = create_cataloging_job(
                db,
                self.project_id,
                "auto",
                "opencode_cli:opencode/big-pickle",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            run = job.chapter_runs[0]
            env = {
                "SIMING_MANAGED_AGENT_KIND": "cataloging",
                "SIMING_MANAGED_CATALOGING_PROJECT_ID": self.project_id,
                "SIMING_MANAGED_CATALOGING_JOB_ID": job.id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_ID": self.chapter_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
                "SIMING_MANAGED_CATALOGING_STAGE": "facts",
            }
            with patch.dict(os.environ, env, clear=False):
                asyncio.run(get_next_external_cataloging_chapter(
                    db, self.project_id, {"job_id": job.id, "phase": "facts"},
                ))
                asyncio.run(save_external_cataloging_facts(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "facts": [{
                            "fact_type": "chapter_overview",
                            "evidence": chapter.content,
                            "payload": {
                                "summary": "林舟打开旧门并看见门后空间。",
                                "key_events": ["打开旧门"],
                                "scenes": [{"scene_id": 1, "title": "开门"}],
                                "cataloging_characters": [],
                                "anonymous_participants": [],
                                "cataloging_worldbuilding_titles": ["旧门", "门后空间"],
                                "incidental_worldbuilding_mentions": [],
                            },
                        }, {
                            "fact_type": "worldbuilding_fact",
                            "evidence": "林舟打开旧门。",
                            "payload": {
                                "canonical_title_hint": "旧门",
                                "archive_identity": "stable_setting",
                                "stable_setting_change": False,
                            },
                        }, {
                            "fact_type": "worldbuilding_fact",
                            "evidence": "看见门后空间。",
                            "payload": {
                                "canonical_title_hint": "门后空间",
                                "archive_identity": "stable_setting",
                                "stable_setting_change": False,
                            },
                        }],
                    },
                ))
                os.environ["SIMING_MANAGED_CATALOGING_STAGE"] = "candidates"
                asyncio.run(get_next_external_cataloging_chapter(
                    db, self.project_id, {"job_id": job.id, "phase": "candidates"},
                ))
                first = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            {
                                "type": "chapter_summary",
                                "summary_text": (
                                    "林舟推开旧门，看见门后空间里站着另一个自己；"
                                    "他由此开始核查旧门与异常空间的来历和风险。"
                                ),
                                "coverage_manifest": {
                                    "scene_count": 1,
                                    "characters": [],
                                    "worldbuilding": ["旧门", "门后空间"],
                                    "relationships": [],
                                    "character_profiles": [],
                                },
                                "narrative_state": {"events": ["林舟打开旧门。"]},
                                "narrative_review": {"source": "provided", "findings": []},
                            },
                            {
                                "type": "outline_create",
                                "node_type": "chapter",
                                "title": chapter.title,
                                "summary": "林舟打开旧门并看见异常空间。",
                            },
                        ],
                    },
                ))
                self.assertFalse(first["data"]["candidate_set_complete"])

                partial_link = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            {
                                "type": "worldbuilding_update",
                                "id": old_door.id,
                                "title": old_door.title,
                                "content": "林舟在本章打开的旧门。",
                            },
                            {
                                "type": "worldbuilding_update",
                                "id": behind.id,
                                "title": behind.title,
                                "content": "林舟在旧门之后看见的异常空间。",
                            },
                            {
                                "type": "chapter_link",
                                "worldbuilding_titles": ["旧门"],
                                "description": "本章先关联旧门。",
                            },
                        ],
                    },
                ))
                self.assertFalse(partial_link["data"]["candidate_set_complete"])
                self.assertIn(
                    "chapter_link candidates for declared characters/worldbuilding (1/2)",
                    partial_link["data"]["missing_required_items"],
                )

                amended = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_link",
                            "worldbuilding_titles": ["门后空间"],
                            "locations": ["门后空间"],
                            "description": "补充遗漏的门后空间关联。",
                        }],
                    },
                ))

            self.assertEqual(amended["status"], "ok", amended)
            self.assertTrue(amended["data"]["candidate_set_complete"], amended)
            self.assertTrue(amended["data"]["auto_applied"], amended)
            links = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "chapter_link",
            ).all()
            self.assertEqual(len(links), 1)
            payload = json.loads(links[0].raw_payload)
            self.assertEqual(
                payload["worldbuilding_titles"],
                ["旧门", "门后空间"],
            )
            self.assertEqual(payload["locations"], ["门后空间"])
            db.refresh(job)
            self.assertEqual(job.status, "completed")
        finally:
            db.close()

    def test_managed_summary_can_replace_an_overdeclared_worldbuilding_alias(self):
        db = self.Session()
        try:
            chapter = db.query(Chapter).filter(Chapter.id == self.chapter_id).one()
            fact_title = "连续监听录音著录体系"
            alias_title = "2014年9月17日连续监听录音（CT-2014-0917-03）"
            chapter.content = (
                f"林舟核读CT-2014-0917-03磁带，确认它属于{fact_title}，"
                "也是二〇一四年九月十七日连续监听录音。"
            )
            recording = WorldbuildingEntry(
                project_id=self.project_id,
                dimension="history",
                title="CT-2014-0917-03磁带",
                content="收录二〇一四年九月十七日连续监听内容的既有档案载体。",
                status="active",
            )
            db.add(recording)
            db.commit()

            job = create_cataloging_job(
                db,
                self.project_id,
                "auto",
                "opencode_cli:opencode/big-pickle",
                [self.chapter_id],
                execution_backend="local_cli_agent",
            )
            run = job.chapter_runs[0]
            env = {
                "SIMING_MANAGED_AGENT_KIND": "cataloging",
                "SIMING_MANAGED_CATALOGING_PROJECT_ID": self.project_id,
                "SIMING_MANAGED_CATALOGING_JOB_ID": job.id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_ID": self.chapter_id,
                "SIMING_MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
                "SIMING_MANAGED_CATALOGING_STAGE": "facts",
            }
            with patch.dict(os.environ, env, clear=False):
                asyncio.run(get_next_external_cataloging_chapter(
                    db, self.project_id, {"job_id": job.id, "phase": "facts"},
                ))
                facts = asyncio.run(save_external_cataloging_facts(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "facts": [
                            {
                                "fact_type": "chapter_overview",
                                "evidence": chapter.content,
                                "payload": {
                                    "summary": "林舟核读一盘既有连续监听录音。",
                                    "key_events": ["核读连续监听录音"],
                                    "scenes": [{"scene_id": 1, "title": "核读录音"}],
                                    "cataloging_characters": [],
                                    "anonymous_participants": [],
                                    "cataloging_worldbuilding_titles": [fact_title],
                                    "incidental_worldbuilding_mentions": [],
                                },
                            },
                            {
                                "fact_type": "worldbuilding_fact",
                                "evidence": chapter.content,
                                "payload": {
                                    "canonical_title_hint": fact_title,
                                    "title_hint": "二〇一四年九月十七日连续监听录音",
                                    "archive_identity": "stable_setting",
                                    "stable_setting_change": False,
                                    "description": "既有磁带中的连续监听录音得到核读确认。",
                                    "keywords": [fact_title],
                                },
                            },
                        ],
                    },
                ))
                self.assertEqual(facts["status"], "ok", facts)
                os.environ["SIMING_MANAGED_CATALOGING_STAGE"] = "candidates"
                asyncio.run(get_next_external_cataloging_chapter(
                    db, self.project_id, {"job_id": job.id, "phase": "candidates"},
                ))

                first = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            {
                                "type": "chapter_summary",
                                "summary_text": (
                                    "林舟核读既有磁带内的连续监听录音，将编号、日期与录音内容逐项核对，"
                                    "确认这些说法指向同一份档案载体，并把核读结果留在正式流程中。"
                                ),
                                "coverage_manifest": {
                                    "scene_count": 1,
                                    "characters": [],
                                    "worldbuilding": [recording.title, alias_title],
                                    "relationships": [],
                                    "character_profiles": [],
                                },
                                "narrative_state": {
                                    "events": [{"description": "林舟完成录音核读。"}],
                                },
                                "narrative_review": {
                                    "source": "provided",
                                    "outcome": "assessed",
                                    "findings": [],
                                },
                            },
                            {
                                "type": "outline_create",
                                "node_type": "chapter",
                                "title": chapter.title,
                                "summary": "林舟核读连续监听录音并确认档案身份。",
                            },
                        ],
                    },
                ))
                self.assertFalse(first["data"]["candidate_set_complete"], first)

                partial = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [
                            {
                                "type": "worldbuilding_update",
                                "id": recording.id,
                                "title": recording.title,
                                "source_fact_titles": [fact_title],
                                "content": (
                                    "本章完成核读，确认该磁带收录二〇一四年九月十七日的连续监听内容。"
                                ),
                            },
                            {
                                "type": "chapter_link",
                                "worldbuilding_titles": [recording.title, alias_title],
                                "description": "本章核读并确认这盘既有磁带。",
                            },
                        ],
                    },
                ))
                self.assertFalse(partial["data"]["candidate_set_complete"], partial)
                self.assertIn(
                    "worldbuilding candidates for declared entries (1/2)",
                    partial["data"]["missing_required_items"],
                )

                summary = db.query(CatalogingCandidate).filter(
                    CatalogingCandidate.chapter_run_id == run.id,
                    CatalogingCandidate.item_type == "chapter_summary",
                ).one()
                before_payload = json.loads(summary.raw_payload)

                rejected = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_summary",
                            "coverage_manifest_mode": "replace",
                            "coverage_manifest": {
                                "scene_count": 0,
                                "characters": [],
                                "worldbuilding": [recording.title],
                                "relationships": [],
                                "character_profiles": [],
                            },
                        }],
                    },
                ))
                self.assertEqual(rejected["status"], "skipped", rejected)
                self.assertTrue(any(
                    "scene_count" in item
                    for item in rejected["data"]["validation_errors"]
                ))
                db.refresh(summary)
                self.assertEqual(json.loads(summary.raw_payload), before_payload)

                corrected = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_summary",
                            "coverage_manifest_mode": "replace",
                            "coverage_manifest": {
                                "scene_count": 1,
                                "characters": [],
                                "worldbuilding": [recording.title],
                                "relationships": [],
                                "character_profiles": [],
                            },
                        }],
                    },
                ))

                self.assertEqual(corrected["status"], "ok", corrected)
                self.assertFalse(corrected["data"]["candidate_set_complete"], corrected)
                self.assertTrue(any(
                    "章节关联包含清单外世界观" in item
                    for item in corrected["data"]["missing_required_items"]
                ))

                link = db.query(CatalogingCandidate).filter(
                    CatalogingCandidate.chapter_run_id == run.id,
                    CatalogingCandidate.item_type == "chapter_link",
                ).one()
                before_link_payload = json.loads(link.raw_payload)
                rejected_link = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_link",
                            "chapter_link_mode": "replace",
                            "worldbuilding_titles": [recording.title],
                        }],
                    },
                ))
                self.assertEqual(rejected_link["status"], "skipped", rejected_link)
                self.assertTrue(any(
                    "missing required fields" in item
                    for item in rejected_link["data"]["validation_errors"]
                ))
                db.refresh(link)
                self.assertEqual(json.loads(link.raw_payload), before_link_payload)

                corrected_link = asyncio.run(save_external_cataloging_candidates(
                    db,
                    self.project_id,
                    {
                        "job_id": job.id,
                        "chapter_id": self.chapter_id,
                        "candidates": [{
                            "type": "chapter_link",
                            "chapter_link_mode": "replace",
                            "characters": [],
                            "worldbuilding_titles": [recording.title],
                            "locations": [],
                            "items": [],
                            "events": ["林舟完成录音核读"],
                            "description": "本章核读并确认这盘既有磁带。",
                        }],
                    },
                ))

            self.assertEqual(corrected_link["status"], "ok", corrected_link)
            self.assertTrue(
                corrected_link["data"]["candidate_set_complete"], corrected_link
            )
            self.assertTrue(corrected_link["data"]["auto_applied"], corrected_link)
            summaries = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "chapter_summary",
            ).all()
            self.assertEqual(len(summaries), 1)
            final_payload = json.loads(summaries[0].raw_payload)
            self.assertEqual(
                final_payload["coverage_manifest"]["worldbuilding"],
                [recording.title],
            )
            self.assertNotIn("coverage_manifest_mode", final_payload)
            self.assertEqual(final_payload["summary_text"], before_payload["summary_text"])
            self.assertEqual(final_payload["narrative_state"], before_payload["narrative_state"])
            links = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "chapter_link",
            ).all()
            self.assertEqual(len(links), 1)
            final_link_payload = json.loads(links[0].raw_payload)
            self.assertEqual(
                final_link_payload["worldbuilding_titles"],
                [recording.title],
            )
            self.assertEqual(final_link_payload["events"], ["林舟完成录音核读"])
            self.assertNotIn("chapter_link_mode", final_link_payload)
            world_updates = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "worldbuilding_update",
            ).all()
            self.assertEqual(len(world_updates), 1)
            final_world_payload = json.loads(world_updates[0].raw_payload)
            self.assertEqual(final_world_payload["title"], recording.title)
            self.assertEqual(final_world_payload["source_fact_titles"], [fact_title])
            active_titles = [
                row.title
                for row in db.query(WorldbuildingEntry).filter(
                    WorldbuildingEntry.project_id == self.project_id,
                    WorldbuildingEntry.status == "active",
                ).all()
            ]
            self.assertEqual(active_titles.count(recording.title), 1)
            self.assertNotIn(alias_title, active_titles)
            db.refresh(job)
            self.assertEqual(job.status, "completed")
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
                        stage="facts",
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
        assert permissions[f"siming_turn_{tool_name}"] == "allow"
    assert permissions["siming_turn_set_tool_categories"] == "allow"
    assert "siming_*" not in permissions
