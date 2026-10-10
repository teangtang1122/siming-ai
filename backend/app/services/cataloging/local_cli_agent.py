"""Siming-managed local CLI cataloging coordinator.

Each chapter is handled in a fresh CLI turn. The Agent reads the UTF-8 project
mirror directly and performs every model-originated write through Siming MCP.
This keeps chapter text out of command arguments and avoids carrying an entire
novel through one ever-growing CLI conversation.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import (
    DEFAULT_CLI_COMMANDS,
    DEFAULT_CLI_MODELS,
    OPENCODE_FAMILY_PROVIDERS,
    CLILaunch,
    CLIQuotaLimitError,
    CLIStalledError,
    CLITurnTerminal,
    LocalCLIAdapter,
    communicate_with_cli_quota_detection,
    detect_cli_quota_error,
    ensure_opencode_logging_args,
    hidden_subprocess_kwargs,
    parse_cli_launch,
)
from app.ai.local_cli_prompt import (
    prepare_direct_mcp_launch,
    prepare_opencode_mcp_environment,
    supports_direct_mcp,
)
from app.architecture.uow import commit_session
from app.core.legacy_env import set_compatible_env
from app.database.models import (
    AgentRun,
    AgentRunEvent,
    APIConfig,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    Project,
)
from app.database.session import SessionLocal
from app.modules.story.application.content_sync import ensure_chapter_mirror
from app.prompts.cataloging_source import get_external_cataloging_system_prompt
from app.services.cataloging.job_control import complete_cataloging_job, refresh_job_progress
from app.services.cataloging.local_cli_mcp import (
    opencode_cataloging_permission_env,
)
from app.services.cataloging.local_cli_progress import (
    CHECKPOINT_TERMINAL,
    STALL_PREFIX,
    CandidateProgressProbe,
)
from app.services.cataloging.local_cli_result import (
    agent_tool_event_count,
    handle_cli_turn_exception,
    handle_cli_turn_result,
)
from app.services.external_agent.run_service import add_event, create_run, update_run_status
from app.services.operation_runtime import (
    record_operation_signal,
    register_operation_actions,
    unregister_operation_actions,
)
from app.services.tool_category_state import (
    create_tool_category_state,
    read_tool_category_audits,
    remove_tool_category_state,
)

_COORDINATORS: dict[str, asyncio.Task] = {}
_PROCESSES: dict[str, asyncio.subprocess.Process] = {}
_TERMINAL_JOBS = {"completed", "failed", "cancelled"}
_TERMINAL_RUNS = {"completed", "completed_with_warnings", "skipped_by_user"}
_DEFAULT_CLI_POLL_SECONDS = 5


def _timeout_seconds_from_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _latest_agent_event_at(agent_run_id: str) -> datetime | None:
    db = SessionLocal()
    try:
        row = (
            db.query(AgentRunEvent.created_at)
            .filter(AgentRunEvent.run_id == agent_run_id)
            .order_by(AgentRunEvent.sequence.desc())
            .first()
        )
        return row[0] if row and row[0] else None
    finally:
        db.close()


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/F", "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                **hidden_subprocess_kwargs(),
            )
        except Exception:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except Exception:
        pass


async def _cancel_communicate_task(task: asyncio.Task) -> None:
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _provider_from_model(model: str | None) -> str | None:
    if model and ":" in model:
        return model.split(":", 1)[0].strip() or None
    return None


def _select_cli_config(db: Session, provider: str | None) -> APIConfig | None:
    query = db.query(APIConfig).filter(APIConfig.provider_type == "local_cli")
    if provider:
        return query.filter(APIConfig.provider == provider).first()
    return (
        query.filter(APIConfig.is_global_default == True).first()  # noqa: E712
        or query.order_by(APIConfig.updated_at.desc()).first()
    )


def _active_agent_run(db: Session, job: CatalogingJob, provider: str) -> AgentRun:
    run = None
    if job.agent_run_id:
        run = db.query(AgentRun).filter(AgentRun.id == job.agent_run_id).first()
    if run and run.status not in {"completed", "failed", "cancelled"}:
        run.status = "running"
        run.current_step = "准备处理下一章"
        run.updated_at = datetime.utcnow()
        commit_session(db)
        return run

    run = create_run(
        db,
        job.project_id,
        source="internal_cli",
        client_name=provider,
        title=f"作品建档：{job.total_chapters or 0} 章",
        create_operation=False,
    )
    job.agent_run_id = run.id
    job.updated_at = datetime.utcnow()
    commit_session(db)
    return run


def ensure_local_cli_cataloging_worker(
    db: Session,
    job: CatalogingJob,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Start or resume the background coordinator for a local CLI job."""
    provider = provider or _provider_from_model(job.model)
    config = _select_cli_config(db, provider)
    if not config:
        raise RuntimeError("未找到可用的本机 CLI 配置")
    provider = config.provider
    run = _active_agent_run(db, job, provider)
    job.execution_backend = "local_cli_agent"
    if job.status not in _TERMINAL_JOBS and job.status != "waiting_confirmation":
        job.status = "running"
    commit_session(db)

    current = _COORDINATORS.get(job.id)
    if not current or current.done() or current.cancelling():
        queued_job_id = job.id
        async def coordinate_after_previous():
            if current and not current.done():
                await asyncio.gather(current, return_exceptions=True)
            await _coordinate_cataloging(queued_job_id, provider)
        _COORDINATORS[job.id] = asyncio.create_task(
            coordinate_after_previous(),
            name=f"cataloging-cli-{job.id}",
        )
    if job.operation_id:
        register_operation_actions(
            job.operation_id,
            **{
                "pause": lambda: _pause_cataloging_operation(job.id),
                "continue": lambda: _continue_cataloging_operation(job.id, provider),
                "cancel": lambda: _cancel_cataloging_operation(job.id),
                "retry_current_unit": lambda: _retry_cataloging_operation(job.id, provider),
            },
        )
    return {
        "agent_run_id": run.id,
        "provider": provider,
        "job_id": job.id,
    }


def cancel_local_cli_cataloging_worker(job_id: str, *, terminal: bool = False) -> None:
    process = _PROCESSES.get(job_id)
    task = _COORDINATORS.get(job_id)
    def stop():
        if process and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        if task and not task.done():
            task.cancel()
    if task and task.get_loop().is_running():
        task.get_loop().call_soon_threadsafe(stop)
    else:
        stop()
    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if job and job.agent_run_id:
            run = db.query(AgentRun).filter(AgentRun.id == job.agent_run_id).first()
            if run and run.status not in {"completed", "failed", "cancelled"}:
                run.status = "cancelled" if terminal else "waiting_confirmation"
                run.current_step = "任务已取消" if terminal else "任务已暂停"
                run.completed_at = datetime.utcnow() if terminal else None
                run.updated_at = datetime.utcnow()
                commit_session(db)
    finally:
        db.close()


async def _pause_cataloging_operation(job_id: str) -> None:
    from app.services.cataloging.job_control import pause_job, refresh_job_progress

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOBS:
            return
        pause_job(job)
        refresh_job_progress(db, job)
        commit_session(db)
    finally:
        db.close()


async def _continue_cataloging_operation(job_id: str, provider: str) -> None:
    from app.services.cataloging.job_control import refresh_job_progress, resume_job

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOBS:
            return
        if not resume_job(job):
            return
        refresh_job_progress(db, job)
        commit_session(db)
        ensure_local_cli_cataloging_worker(db, job, provider=provider)
    finally:
        db.close()


async def _cancel_cataloging_operation(job_id: str) -> None:
    from app.services.cataloging.job_control import cancel_job, refresh_job_progress

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job:
            return
        cancel_job(job)
        refresh_job_progress(db, job)
        commit_session(db)
    finally:
        db.close()
    unregister_operation_actions(job.operation_id if job else None)


async def _retry_cataloging_operation(job_id: str, provider: str) -> None:
    from app.services.cataloging.job_control import (
        first_retryable_run,
        refresh_job_progress,
        reset_run_for_retry,
    )

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOBS:
            return
        run = first_retryable_run(db, job)
        if run:
            reset_run_for_retry(db, job, run)
        else:
            job.status = "running"
            job.error = None
            refresh_job_progress(db, job)
        commit_session(db)
        ensure_local_cli_cataloging_worker(db, job, provider=provider)
    finally:
        db.close()


def local_cli_cataloging_is_running(job_id: str) -> bool:
    task = _COORDINATORS.get(job_id)
    return bool(task and not task.done())


def _next_run(db: Session, job_id: str) -> CatalogingChapterRun | None:
    return (
        db.query(CatalogingChapterRun)
        .filter(CatalogingChapterRun.job_id == job_id)
        .filter(CatalogingChapterRun.status.notin_(list(_TERMINAL_RUNS)))
        .order_by(CatalogingChapterRun.chapter_order.asc())
        .first()
    )


def _ensure_chapter_file(
    db: Session,
    project: Project,
    chapter: Chapter,
    chapter_order: int,
) -> tuple[Path, Path]:
    return ensure_chapter_mirror(
        db,
        project,
        chapter,
        index=chapter_order + 1,
        source="local_cli_cataloging",
    )


def _turn_stage(run: CatalogingChapterRun, mode: str) -> str:
    return "apply" if run.status == "awaiting_confirmation" and mode == "auto" else "planning"


def _task_text(
    *,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    agent_run_id: str,
    provider: str,
    project: Project,
    project_folder: Path,
    chapter: Chapter,
    chapter_file: Path,
    stage: str,
) -> str:
    shared_prompt = get_external_cataloging_system_prompt()
    managed_override = ""
    if stage == "apply":
        stage_steps = f"""
## 本轮唯一任务
0. 立即调用 `report_agent_plan`，上报本轮计划：读取控制状态、应用候选、验证进度。
1. 调用 `get_cataloging_control_state`，参数必须包含：
   `project_id="{job.project_id}"`, `job_id="{job.id}"`, `run_id="{agent_run_id}"`。
2. 只有 execution_mode 仍为 `auto` 时，调用 `apply_pending_cataloging` 写入当前候选。
3. 调用 `verify_external_cataloging_progress`，然后结束本轮。
4. 禁止再次领取或处理下一章；下一章必须由司命启动全新的 CLI 回合。
"""
    else:
        stage_steps = f"""
## 本轮唯一任务：完成本章建档计划
0. 所需类别开放后，用 report_agent_plan 上报读取、规划、提交和验证步骤。
1. get_next_external_cataloging_chapter(job_id="{job.id}", include_content=false, include_prompt_pack=false)。
2. 只处理绑定的 chapter_id="{chapter.id}"，阅读本章文件与需要的真实档案；可通过 read_cataloging_archive 取得数据库权威记录。
3. 由你统一决定身份、场景与变更，先保存摘要计划，再分批提交相关候选；完整后 finalize=true。
4. 返回错误时只修正失败字段或对象。auto_applied=true 后只 verify_external_cataloging_progress 一次并结束；manual 等待作者确认。
5. 禁止处理下一章；下一章由司命开启新的回合。
"""

    return f"""# 司命本机 CLI 作品建档任务

## 固定身份
你是司命启动的作品建档 Agent，不是代码助手。始终使用中文。
你必须直接读取小说文件，不得要求司命把完整章节塞进提示词或 MCP 返回值。

## 任务绑定
- project_id: `{job.project_id}`
- project_title: `{project.title}`
- cataloging_job_id: `{job.id}`
- chapter_run_id: `{run.id}`
- chapter_id: `{chapter.id}`
- chapter_order: `{run.chapter_order}`
- chapter_title: `{chapter.title}`
- chapter_file: `{chapter_file}`
- project_folder: `{project_folder}`
- agent_run_id: `{agent_run_id}`
- provider: `{provider}`

## 数据边界
- 数据库是唯一权威写入源；项目目录是只读镜像。
- 可以使用文件读取、Glob、Grep 搜索 `{project_folder}`。
- 禁止直接修改 `chapters/`、`characters/`、`worldbuilding/`、`outline/`、`relationships/`。
- 所有候选和应用操作必须调用 Siming MCP 工具。
- 每个 MCP 调用都必须带 `project_id="{job.project_id}"` 和 `run_id="{agent_run_id}"`。
- 不要创建 candidates.jsonl、临时档案或其他旁路数据文件。

## 任务工具
系统已直接开放本章建档和进度上报所需工具，无需选择类别。
按下方步骤执行；其他作品能力不在本任务授权范围内。

{stage_steps}

## 共享建档提示词
{shared_prompt}

{managed_override}

## 输出约束
不要在最终回复里复制章节、完整候选。只简短说明本章处理结果；正式数据必须已经通过 MCP 保存。
"""


def _task_prompt(
    task_file: Path,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    chapter: Chapter,
    agent_run_id: str,
    stage: str,
) -> str:
    return (
        "立即执行，不要向用户提问，也不要等待补充信息。所有任务绑定已经完整给出。\n"
        "你是司命本机作品建档 Agent。本轮是全新的单章任务，禁止沿用任何旧会话或旧章节绑定。\n"
        f"当前阶段={stage}；job_id={job.id}；agent_run_id={agent_run_id}；"
        f"chapter_run_id={run.id}；chapter_id={chapter.id}；章节={chapter.title}。\n"
        "首先调用 report_agent_plan，"
        "然后严格按附件任务文件执行 MCP 工具链。"
        "不得回答“请告知章节”“是否沿用任务”或任何澄清问题。\n"
        "唯一允许读取的任务文件如下；缓存、历史或目录里的其他任务文件全部忽略：\n"
        f"{task_file}\n"
        "章节正文和档案由你从任务指定的作品目录自行读取；所有写入必须使用 Siming MCP。"
    )


def _build_cataloging_cli_launch(
    *,
    config: APIConfig,
    prompt: str,
    model: str,
    task_file: Path,
    project_folder: Path,
    run: CatalogingChapterRun,
) -> CLILaunch:
    launch = parse_cli_launch(config.cli_args, config.provider, prompt, model)
    if config.provider not in OPENCODE_FAMILY_PROVIDERS:
        return launch

    args = list(launch.args)
    ensure_opencode_logging_args(config.provider, args)
    prompt_index = args.index(prompt) if prompt in args else len(args)
    options: list[str] = []
    if "--dir" not in args:
        options.extend(["--dir", str(project_folder)])
    if "--file" not in args:
        options.extend(["--file", str(task_file)])
    if "--title" not in args:
        unique_suffix = datetime.utcnow().strftime("%H%M%S%f")
        options.extend([
            "--title",
            f"Siming cataloging {run.chapter_order + 1:04d} {run.id[:8]} {unique_suffix}",
        ])
    if options:
        args[prompt_index:prompt_index] = options
    return CLILaunch(args=args, stdin_text=launch.stdin_text)


async def _run_cli_turn(
    *,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    project: Project,
    chapter: Chapter,
    config: APIConfig,
    agent_run_id: str,
    stage: str,
) -> tuple[int, str, str]:
    db = SessionLocal()
    try:
        db_project = db.query(Project).filter(Project.id == project.id).first()
        db_chapter = db.query(Chapter).filter(Chapter.id == chapter.id).first()
        project_folder, chapter_file = _ensure_chapter_file(
            db,
            db_project,
            db_chapter,
            run.chapter_order,
        )
    finally:
        db.close()

    run_dir = project_folder / ".siming" / "cataloging" / job.id
    run_dir.mkdir(parents=True, exist_ok=True)
    task_file = run_dir / f"{run.chapter_order + 1:04d}-{stage}.md"
    task_file.write_text(
        _task_text(
            job=job,
            run=run,
            agent_run_id=agent_run_id,
            provider=config.provider,
            project=project,
            project_folder=project_folder,
            chapter=chapter,
            chapter_file=chapter_file,
            stage=stage,
        ),
        encoding="utf-8",
        newline="\n",
    )

    command = (config.cli_command or DEFAULT_CLI_COMMANDS.get(config.provider) or "").strip()
    resolved = shutil.which(command) or (command if Path(command).exists() else None)
    if not resolved:
        raise RuntimeError(f"未找到本机 CLI 命令：{command}")
    model = (
        (job.model.split(":", 1)[1] if job.model and ":" in job.model else job.model)
        or config.default_model or DEFAULT_CLI_MODELS.get(config.provider, config.provider)
    )
    env = os.environ.copy()
    env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
    managed_env = {
        "MANAGED_AGENT_KIND": "cataloging",
        "MANAGED_CATALOGING_PROJECT_ID": job.project_id,
        "MANAGED_CATALOGING_JOB_ID": job.id,
        "MANAGED_CATALOGING_CHAPTER_ID": chapter.id,
        "MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
        "MANAGED_CATALOGING_AGENT_RUN_ID": agent_run_id,
        "MANAGED_CATALOGING_STAGE": stage,
    }
    for suffix, value in managed_env.items():
        set_compatible_env(f"SIMING_{suffix}", value, target=env)

    # Keep the shared audit transport; this fixed task has no category selector.
    category_file = create_tool_category_state()
    try:
        progress_probe = CandidateProgressProbe(
            category_file=category_file, chapter_run_id=run.id,
            session_factory=SessionLocal,
        ) if stage == "planning" else None
        prompt = _task_prompt(task_file, job, run, chapter, agent_run_id, stage)
        launch = _build_cataloging_cli_launch(
            config=config, prompt=prompt, model=model, task_file=task_file,
            project_folder=project_folder, run=run,
        )
        step_env = dict(env)
        if config.provider in OPENCODE_FAMILY_PROVIDERS:
            step_env = prepare_opencode_mcp_environment(
                provider=config.provider, cwd=str(run_dir), base_env=step_env,
                permission_pack="cataloging_worker", project_id=job.project_id,
                tool_category_state_file=category_file,
                permissions=json.loads(opencode_cataloging_permission_env()),
            )
        elif supports_direct_mcp(config.provider):
            launch, step_env = prepare_direct_mcp_launch(
                LocalCLIAdapter(api_key="", base_url=config.provider), launch,
                cwd=str(run_dir), env=step_env,
                permission_pack="cataloging_worker", project_id=job.project_id,
                tool_category_state_file=category_file,
            )
        try:
            result = await _execute_cataloging_cli_step(
                resolved=resolved, launch=launch, env=step_env,
                project_folder=project_folder, job=job, run=run, chapter=chapter,
                model=model, agent_run_id=agent_run_id, stage=stage,
                category_file=category_file,
                progress_probe=progress_probe,
            )
        except CLITurnTerminal as exc:
            if str(exc).startswith(STALL_PREFIX):
                raise CLIStalledError(str(exc)[len(STALL_PREFIX):]) from exc
            if str(exc) == CHECKPOINT_TERMINAL:
                return 0, exc.stdout, exc.stderr
            raise
        return result
    finally:
        try:
            (run_dir / f"{run.chapter_order + 1:04d}-{stage}-category-audit.json").write_text(
                json.dumps(read_tool_category_audits(category_file), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        finally:
            remove_tool_category_state(category_file)


async def _execute_cataloging_cli_step(
    *, resolved: str, launch: CLILaunch, env: dict[str, str], project_folder: Path,
    job: CatalogingJob, run: CatalogingChapterRun, chapter: Chapter, model: str,
    agent_run_id: str, stage: str, category_file: str, progress_probe=None,
) -> tuple[int, str, str]:
    """Execute the fixed task until completion, cancellation or a checkpoint."""
    process = await asyncio.create_subprocess_exec(
        resolved,
        *launch.args,
        stdin=asyncio.subprocess.PIPE if launch.stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_folder),
        env=env,
        **hidden_subprocess_kwargs(),
    )
    _PROCESSES[job.id] = process
    poll_seconds = _timeout_seconds_from_env(
        "SIMING_CATALOGING_CLI_POLL_SECONDS",
        _DEFAULT_CLI_POLL_SECONDS,
    )
    if job.operation_id:
        operation_db = SessionLocal()
        try:
            record_operation_signal(
                job.operation_id,
                "phase",
                {
                    "phase": stage,
                    "current_object": f"第 {run.chapter_order + 1} 章：{chapter.title}",
                    "model": model,
                    "pid": process.pid,
                },
                message=f"正在处理第 {run.chapter_order + 1} 章：{chapter.title}",
                db=operation_db,
            )
        finally:
            operation_db.close()
    try:
        stdout, stderr = await communicate_with_cli_quota_detection(
            process,
            input_bytes=launch.stdin_text.encode("utf-8") if launch.stdin_text is not None else None,
            timeout_seconds=None,
            operation_id=job.operation_id,
            external_activity_probe=lambda: _latest_agent_event_at(agent_run_id),
            terminal_probe=progress_probe,
            poll_seconds=poll_seconds,
            # This worker owns an explicitly authorized, process-scoped MCP
            # configuration. Its stdout/stderr may contain arbitrary novel
            # prose read from disk, including sentences such as "是否允许摘录".
            # Treating those words as a transport approval prompt corrupts
            # story data into process control. Real MCP denial is returned by
            # the structured tool result; liveness remains monitor-owned.
            stop_on_permission_request=False,
        )
    except CLIQuotaLimitError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        _PROCESSES.pop(job.id, None)
    out_text = stdout.decode("utf-8", errors="replace").strip()
    err_text = stderr.decode("utf-8", errors="replace").strip()
    quota_error = detect_cli_quota_error(err_text, out_text)
    if quota_error:
        raise RuntimeError(quota_error)
    return (
        process.returncode or 0,
        out_text,
        err_text,
    )


def _finalize_completed_sidecars(db: Session, job: CatalogingJob) -> None:
    """Close the Agent/operation records after MCP finishes the last chapter."""

    complete_cataloging_job(db, job)
    if job.operation_id:
        unregister_operation_actions(job.operation_id)
    commit_session(db)


async def _coordinate_cataloging(job_id: str, provider: str) -> None:
    from .job_control import validate_cataloging_run_source

    no_save_attempts: dict[str, int] = {}
    try:
        while True:
            db = SessionLocal()
            try:
                job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
                if not job:
                    return
                if job.status in _TERMINAL_JOBS:
                    _finalize_terminal_sidecars(db, job)
                    return
                if job.status == "paused":
                    return
                config = _select_cli_config(db, provider)
                if not config:
                    raise RuntimeError(f"本机 CLI 配置不存在：{provider}")
                agent_run = _active_agent_run(db, job, provider)
                run = _next_run(db, job.id)
                if not run:
                    job.status = "completed"
                    job.current_chapter_id = None
                    job.blocked_chapter_id = None
                    job.completed_at = datetime.utcnow()
                    _finalize_completed_sidecars(db, job)
                    return
                if run.status == "failed":
                    _pause_failed_run(db, job, run, agent_run)
                    return
                if run.status == "awaiting_confirmation" and job.execution_mode == "manual":
                    job.status = "waiting_confirmation"
                    job.blocked_chapter_id = run.chapter_id
                    agent_run.status = "waiting_confirmation"
                    agent_run.current_step = f"等待确认：第 {run.chapter_order + 1} 章"
                    commit_session(db)
                    return
                project = db.query(Project).filter(Project.id == job.project_id).first()
                chapter = validate_cataloging_run_source(db, job, run)
                if not project or not chapter:
                    raise RuntimeError("建档任务关联的作品或章节不存在")
                stage = _turn_stage(run, job.execution_mode)
                run.started_at = run.started_at or datetime.utcnow()
                job.status = "running"
                job.current_chapter_id = chapter.id
                job.blocked_chapter_id = None
                agent_run.status = "running"
                agent_run.current_step = f"第 {run.chapter_order + 1} 章：{stage}"
                commit_session(db)
                if job.operation_id:
                    record_operation_signal(
                        job.operation_id,
                        "phase",
                        {
                            "phase": stage,
                            "chapter_id": chapter.id,
                            "chapter_order": run.chapter_order,
                            "current_object": chapter.title,
                        },
                        message=f"开始处理第 {run.chapter_order + 1} 章：{chapter.title}",
                        db=db,
                    )
                add_event(
                    db,
                    agent_run.id,
                    "chapter_agent_started",
                    status="running",
                    message=f"开始处理第 {run.chapter_order + 1} 章：{chapter.title}",
                    payload_json=json.dumps({
                        "job_id": job.id,
                        "chapter_id": chapter.id,
                        "chapter_run_id": run.id,
                        "stage": stage,
                    }, ensure_ascii=False),
                )
                # The CLI turn outlives this database session. Refresh and
                # detach the scalar snapshots so later access never triggers a
                # lazy load on a closed Session.
                snapshots = (job, run, project, chapter, config)
                for snapshot in snapshots:
                    db.refresh(snapshot)
                    db.expunge(snapshot)
                job_snapshot = job
                run_snapshot = run
                project_snapshot = project
                chapter_snapshot = chapter
                config_snapshot = config
                agent_run_id = agent_run.id
            finally:
                db.close()

            tool_events_before = agent_tool_event_count(
                agent_run_id,
                session_factory=SessionLocal,
            )
            try:
                returncode, stdout, stderr = await _run_cli_turn(
                    job=job_snapshot,
                    run=run_snapshot,
                    project=project_snapshot,
                    chapter=chapter_snapshot,
                    config=config_snapshot,
                    agent_run_id=agent_run_id,
                    stage=stage,
                )
            except Exception as exc:
                action = handle_cli_turn_exception(
                    job_id=job_id,
                    chapter_run_id=run_snapshot.id,
                    agent_run_id=agent_run_id,
                    stage=stage,
                    exc=exc,
                    session_factory=SessionLocal,
                )
                if action == "return":
                    return
                continue

            action = await handle_cli_turn_result(
                job_id=job_id,
                chapter_run_id=run_snapshot.id,
                agent_run_id=agent_run_id,
                chapter_title=chapter_snapshot.title,
                stage=stage,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                tool_events_before=tool_events_before,
                no_save_attempts=no_save_attempts,
                session_factory=SessionLocal,
            )
            if action == "return":
                return
            if action == "continue":
                continue
    except asyncio.CancelledError:
        return
    except Exception as exc:
        db = SessionLocal()
        try:
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            if job and job.status in {"queued", "running"}:
                job.status = "paused_on_failure"
                job.error = str(exc)
                refresh_job_progress(db, job)
                commit_session(db)
                if job.agent_run_id:
                    add_event(db, job.agent_run_id, "error", status="error", message=str(exc))
        finally:
            db.close()
    finally:
        if _COORDINATORS.get(job_id) is asyncio.current_task():
            _COORDINATORS.pop(job_id, None)


def _finalize_terminal_sidecars(db: Session, job: CatalogingJob) -> None:
    if job.status == "completed":
        _finalize_completed_sidecars(db, job)
    else:
        refresh_job_progress(db, job)
        commit_session(db)


def _pause_failed_run(
    db: Session, job: CatalogingJob, run: CatalogingChapterRun, agent_run: AgentRun,
) -> None:
    job.status = "paused_on_failure"
    job.blocked_chapter_id = run.chapter_id
    job.error = run.error
    refresh_job_progress(db, job)
    commit_session(db)
    update_run_status(db, agent_run.id, "failed", summary=run.error or "当前章节建档失败")
