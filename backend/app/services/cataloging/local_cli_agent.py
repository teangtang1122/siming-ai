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
    communicate_with_cli_quota_detection,
    detect_cli_quota_error,
    effective_local_cli_model,
    ensure_opencode_logging_args,
    hidden_subprocess_kwargs,
    parse_cli_launch,
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
from app.services.cataloging.candidate_io import candidate_to_dict
from app.services.cataloging.fact_store import fact_to_dict
from app.services.cataloging.job_control import refresh_job_progress
from app.services.cataloging.local_cli_mcp import (
    opencode_cataloging_permission_env,
    preflight_opencode_cataloging,
)
from app.services.cataloging.local_cli_result import (
    agent_tool_event_count,
    handle_cli_turn_exception,
    handle_cli_turn_result,
)
from app.services.cataloging.orchestrator import job_to_dict, run_to_dict, sse_event
from app.services.external_agent.run_service import add_event, create_run, update_run_status
from app.services.operation_runtime import (
    finish_operation,
    record_operation_signal,
    register_operation_actions,
    unregister_operation_actions,
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
    mcp_preflight = None
    if provider == "opencode_cli":
        mcp_preflight = preflight_opencode_cataloging(config.cli_command)
        if not mcp_preflight.get("ready"):
            raise RuntimeError(
                "OpenCode 无法开始 MCP 建档："
                + str(mcp_preflight.get("detail") or "MCP 启动检查未通过")
            )
    run = _active_agent_run(db, job, provider)
    job.execution_backend = "local_cli_agent"
    if job.status not in _TERMINAL_JOBS and job.status != "waiting_confirmation":
        job.status = "running"
    commit_session(db)

    current = _COORDINATORS.get(job.id)
    if not current or current.done():
        _COORDINATORS[job.id] = asyncio.create_task(
            _coordinate_cataloging(job.id, provider),
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
        "mcp_preflight": mcp_preflight,
    }


def cancel_local_cli_cataloging_worker(job_id: str, *, terminal: bool = False) -> None:
    process = _PROCESSES.get(job_id)
    if process and process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    task = _COORDINATORS.get(job_id)
    if task and not task.done():
        task.cancel()
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
    cancel_local_cli_cataloging_worker(job_id, terminal=False)


async def _continue_cataloging_operation(job_id: str, provider: str) -> None:
    from app.services.cataloging.job_control import refresh_job_progress, resume_job

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOBS:
            return
        resume_job(job)
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
    cancel_local_cli_cataloging_worker(job_id, terminal=True)
    unregister_operation_actions(job.operation_id if job else None)


async def _retry_cataloging_operation(job_id: str, provider: str) -> None:
    from app.services.cataloging.job_control import first_blocking_run, refresh_job_progress, reset_run_for_retry

    db = SessionLocal()
    try:
        job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
        if not job or job.status in _TERMINAL_JOBS:
            return
        run = first_blocking_run(db, job)
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


async def stream_local_cli_cataloging_job(project_id: str, job_id: str):
    """Stream database and AgentRun changes using the existing cataloging UI contract."""
    from app.database.models import AgentRunEvent, CatalogingCandidate, CatalogingFact

    db = SessionLocal()
    seen_facts: set[str] = set()
    seen_candidates: set[str] = set()
    seen_run_states: dict[str, str] = {}
    last_agent_sequence = 0
    last_job_signature: tuple[Any, ...] | None = None
    try:
        job = db.query(CatalogingJob).filter(
            CatalogingJob.id == job_id,
            CatalogingJob.project_id == project_id,
        ).first()
        if not job:
            yield sse_event({"type": "error", "message": "作品建档任务不存在"})
            yield "data: [DONE]\n\n"
            return
        seen_facts = {
            row.id
            for row in db.query(CatalogingFact.id)
            .filter(CatalogingFact.job_id == job.id)
            .all()
        }
        seen_candidates = {
            row.id
            for row in db.query(CatalogingCandidate.id)
            .filter(CatalogingCandidate.job_id == job.id)
            .all()
        }
        if job.status not in _TERMINAL_JOBS and job.status not in {"paused", "waiting_confirmation"}:
            ensure_local_cli_cataloging_worker(db, job)
        yield sse_event({
            "type": "cataloging_stage",
            "message": "本机 CLI Agent 已连接，Siming MCP 建档工具已通过启动检查",
            "job": job_to_dict(job),
        })

        while True:
            await asyncio.sleep(0.5)
            db.expire_all()
            job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
            if not job:
                yield sse_event({"type": "error", "message": "作品建档任务已被删除"})
                yield "data: [DONE]\n\n"
                return

            runs = (
                db.query(CatalogingChapterRun)
                .filter(CatalogingChapterRun.job_id == job.id)
                .order_by(CatalogingChapterRun.chapter_order.asc())
                .all()
            )
            for run in runs:
                previous = seen_run_states.get(run.id)
                if previous != run.status:
                    seen_run_states[run.id] = run.status
                    event_type = "chapter_started" if run.status in {"in_progress", "extracting"} else "chapter_state"
                    if run.status in _TERMINAL_RUNS:
                        event_type = "chapter_completed"
                    elif run.status == "failed":
                        event_type = "chapter_failed"
                    yield sse_event({
                        "type": event_type,
                        "message": f"第 {run.chapter_order + 1} 章：{run.status}",
                        "job": job_to_dict(job),
                        "run": run_to_dict(run),
                    })

            facts = (
                db.query(CatalogingFact)
                .filter(CatalogingFact.job_id == job.id)
                .order_by(CatalogingFact.created_at.asc())
                .all()
            )
            for fact in facts:
                if fact.id in seen_facts:
                    continue
                seen_facts.add(fact.id)
                payload = fact_to_dict(fact)
                yield sse_event({
                    "type": "fact_extracted",
                    "message": f"已抽取事实：{fact.fact_type}",
                    "fact": {
                        "fact_type": fact.fact_type,
                        "payload": payload.get("payload") or {},
                        "confidence": fact.confidence,
                        "evidence": fact.evidence,
                    },
                    "run": run_to_dict(fact.chapter_run),
                    "job": job_to_dict(job),
                })

            candidates = (
                db.query(CatalogingCandidate)
                .filter(CatalogingCandidate.job_id == job.id)
                .order_by(CatalogingCandidate.created_at.asc())
                .all()
            )
            for candidate in candidates:
                if candidate.id in seen_candidates:
                    continue
                seen_candidates.add(candidate.id)
                yield sse_event({
                    "type": "candidate_created",
                    "message": f"已生成候选：{candidate.item_type}",
                    "candidate": candidate_to_dict(candidate),
                    "run": run_to_dict(candidate.chapter_run),
                    "job": job_to_dict(job),
                })

            if job.agent_run_id:
                events = (
                    db.query(AgentRunEvent)
                    .filter(
                        AgentRunEvent.run_id == job.agent_run_id,
                        AgentRunEvent.sequence > last_agent_sequence,
                    )
                    .order_by(AgentRunEvent.sequence.asc())
                    .all()
                )
                for event in events:
                    last_agent_sequence = max(last_agent_sequence, event.sequence)
                    yield sse_event({
                        "type": "agent_event",
                        "message": event.message or event.event_type,
                        "agent_event": {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "status": event.status,
                            "payload_json": event.payload_json,
                        },
                        "job": job_to_dict(job),
                    })

            signature = (
                job.status,
                job.current_chapter_id,
                job.blocked_chapter_id,
                job.completed_chapters,
                job.failed_chapters,
                job.error,
            )
            if signature != last_job_signature:
                last_job_signature = signature
                yield sse_event({"type": "job", "job": job_to_dict(job)})

            if job.status == "completed":
                yield sse_event({"type": "completed", "job": job_to_dict(job)})
                yield "data: [DONE]\n\n"
                return
            if job.status == "waiting_confirmation" and job.execution_mode == "manual":
                blocking = next((run for run in runs if run.chapter_id == job.blocked_chapter_id), None)
                yield sse_event({
                    "type": "waiting_confirmation",
                    "job": job_to_dict(job),
                    "run": run_to_dict(blocking) if blocking else None,
                })
                yield "data: [DONE]\n\n"
                return
            if job.status in {"paused_on_failure", "paused", "cancelled", "failed"}:
                blocking = next((run for run in runs if run.chapter_id == job.blocked_chapter_id), None)
                yield sse_event({
                    "type": job.status,
                    "job": job_to_dict(job),
                    "run": run_to_dict(blocking) if blocking else None,
                    "error": job.error,
                })
                yield "data: [DONE]\n\n"
                return
    finally:
        db.close()


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
    if run.status == "awaiting_confirmation" and mode == "auto":
        return "apply"
    if run.status == "facts_saved":
        return "candidates"
    return "merged"


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
    if stage == "merged":
        shared_prompt += """

## Experimental Single-Stage Override
This CLI turn uses the merged cataloging experiment. The stage instructions in
this task file override any older two-stage facts/candidates workflow in the
shared prompt. Do not call `save_external_cataloging_facts` or
`list_cataloging_facts`. Read the chapter and archive files directly, then call
`save_external_cataloging_candidates` with `phase="merged"`.
"""
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
        if stage == "merged":
            stage_steps = f"""
## 本轮唯一任务：单阶段建档，直接生成候选
0. 立即调用 `report_agent_plan`，上报本轮计划：领取 merged 阶段章节、读取章节文件、读取全部角色/世界观/大纲镜像、生成候选、按模式应用或等待确认、验证进度。
1. 调用 `get_next_external_cataloging_chapter`：
   - `project_id="{job.project_id}"`
   - `job_id="{job.id}"`
   - `phase="merged"`
   - `include_content=false`
   - `include_prompt_pack=false`
   - `include_context_indexes=false`
   - `run_id="{agent_run_id}"`
2. 工具返回的 chapter_id 必须是 `{chapter.id}`。若不一致，立即停止并说明阻塞。
3. 调用 `report_agent_progress` 说明正在读取当前章节和档案镜像。
4. 直接读取 `chapter_file` 指向的章节正文，并读取 `{project_folder}` 下的 `characters/`、`worldbuilding/`、`outline/`、`summaries/` 等镜像文件。不要要求司命把正文或卡片粘贴进提示词。
5. 关注点与第一阶段事实抽取相同：只采集会影响大纲、角色、关系、世界观或后续连续性的内容；但不要输出 fact，不要调用 `save_external_cataloging_facts` 或 `list_cataloging_facts`。
6. 直接调用一次 `save_external_cataloging_candidates` 保存本章候选，参数必须包含 `phase="merged"`；同一次调用的 candidates 数组开头必须同时包含 chapter_summary、chapter 级 outline_create，不能只保存摘要后结束。chapter_summary 必须包含非空 summary_text、完整 narrative_state、narrative_review，以及 `coverage_manifest={{"scene_count": 独立场景数, "characters": [全部连续性角色名], "worldbuilding": [全部关键设定标题], "relationships": [{{"source_name":"角色A","target_name":"角色B","relationship_type":"关系"}}], "character_profiles": [本章新建或稳定档案变化的角色名]}}`，没有对应内容也必须显式写 []，不得虚构补卡。
   同一角色在 coverage_manifest、状态卡、资料卡、关系端点和章节关联中必须统一使用角色卡稳定主名；昵称、亲属称谓和化名只写 aliases。禁止使用“主名（别名）”组合展示名，也禁止同一批候选在主名、别名之间切换。
   `coverage_manifest` 是强制验收清单：系统按身份逐项核对，不按总数凑卡。每个角色必须有同名 character_state_update，每项设定必须有同标题 worldbuilding_create/update/timeline，每项关系必须有同端点同类型 character_relationship，每个 character_profiles 角色必须有 character_create/update，每个角色和设定必须被 chapter_link 覆盖；scene_count 大于 1 时必须为每个场景创建一条含场景状态字段的 section outline。新角色必须先生成完整角色档案，关系卡不得顺带制造空白角色。数量或身份不足不能结束本章。
   解决伏笔或叙事债务时必须引用已有治理项的 resolves_item_id 或 resolves_dedupe_key；找不到稳定引用时标记待复核，不得按标题猜测关闭。
   每个本章出场或状态变化的角色，都必须保存 `character_state_update`；其中 `appearance` 与 `age` 是逐章状态字段，即使只是沿用上一章当前值也要填写，发生时间线变化时必须改成新状态。
7. 严格读取保存工具返回值：
   - `candidate_set_complete=false`：只补齐 `missing_required_items` 后再次保存；禁止调用 apply 或宣布完成。
   - `auto_applied=true`：候选已在同一事务中自动写入，禁止再次 save/apply。
   - `chapter_run_status=awaiting_confirmation`：当前为手动确认，禁止应用并立即停止。
8. 仅在 `auto_applied=true` 后调用一次 `verify_external_cataloging_progress`，随后立即结束当前 CLI 回合。
9. 禁止再次调用 `get_next_external_cataloging_chapter`，禁止处理下一章；下一章由司命启动全新的 CLI 回合。
"""
        elif stage == "full":
            stage_steps = f"""
## 本轮唯一任务：只保存事实，不生成候选
0. 立即调用 `report_agent_plan`，上报本轮计划：读取控制状态、领取 facts 阶段章节、读取章节文件、保存事实、验证进度。
1. 调用 `get_next_external_cataloging_chapter`：
   - `project_id="{job.project_id}"`
   - `job_id="{job.id}"`
   - `phase="facts"`
   - `include_content=false`
   - `include_prompt_pack=false`
   - `include_context_indexes=false`
   - `run_id="{agent_run_id}"`
2. 工具返回的 chapter_id 必须是 `{chapter.id}`。若不一致，立即停止并说明阻塞。
3. 调用 `report_agent_progress` 说明正在读取章节文件；随后裸读章节文件。
4. 按共享提示词抽取不限数量的事实；调用 `save_external_cataloging_facts` 保存。
   事实必须充分覆盖章节，不得为了缩短 JSON 而漏信息。
5. 调用 `verify_external_cataloging_progress`，然后结束本轮。
6. 本轮禁止调用 `save_external_cataloging_candidates`、`apply_pending_cataloging`，
   禁止处理下一章；候选阶段必须由司命启动下一次 CLI 回合。
"""
        else:
            phase = "candidates"
            fact_steps = """
3. 本章事实已经保存。调用 `report_agent_progress` 说明正在恢复第二阶段。
4. 调用 `list_cataloging_facts`，使用本任务中的 chapter_run_id
   读取事实，再结合相关角色、世界观和大纲镜像生成候选。
"""
            stage_steps = f"""
## 本轮执行步骤
0. 立即调用 `report_agent_plan`，上报本轮将读取文件、保存结构化结果并验证进度。
1. 调用 `get_next_external_cataloging_chapter`：
   - `project_id="{job.project_id}"`
   - `job_id="{job.id}"`
   - `phase="{phase}"`
   - `include_content=false`
   - `include_prompt_pack=false`
   - `include_context_indexes=false`
   - `run_id="{agent_run_id}"`
2. 工具返回的 chapter_id 必须是 `{chapter.id}`。若不一致，立即停止并说明阻塞。
{fact_steps}
6. 直接读取本作品镜像中与事实有关的角色、世界观、大纲文件，合并旧信息后生成候选；
   同一次调用 `save_external_cataloging_candidates` 时，candidates 数组开头必须同时包含 chapter_summary、章级大纲，不能只保存摘要后结束；chapter_summary 必须包含非空 summary_text、完整 narrative_state、narrative_review，以及 `coverage_manifest={{"scene_count": 独立场景数, "characters": [全部连续性角色名], "worldbuilding": [全部关键设定标题], "relationships": [明确且影响连续性的关系对象], "character_profiles": [本章新建或稳定档案变化的角色名]}}`，所有空项也显式写 []，不得虚构补卡。系统按角色名、设定标题和关系端点逐项验收，不接受重复候选凑数。
   同一角色必须统一使用稳定主名；别名和称谓只写 aliases，不得使用“主名（别名）”组合展示名或在同批候选里切换身份写法。
   每个清单角色都要有 character_state_update，每项清单设定都要有 worldbuilding 候选并分别建立 chapter_link；每个独立场景都要创建含场景状态字段的 section 大纲。
   解决伏笔或叙事债务时必须引用已有治理项的 resolves_item_id 或 resolves_dedupe_key；找不到稳定引用时标记待复核，不得按标题猜测关闭。
   每个本章出场或状态变化的角色，都必须保存 `character_state_update`；其中 `appearance` 与 `age` 是逐章状态字段，即使只是沿用上一章当前值也要填写，发生时间线变化时必须改成新状态。
7. 读取保存返回值；不完整时只补齐 missing_required_items 并再次保存；auto_applied=true 时禁止再次 save/apply；等待确认时立即停止。
8. 仅在 auto_applied=true 后调用一次 `verify_external_cataloging_progress`，然后结束本轮。
9. 验证完成后必须立即结束当前 CLI 回合。禁止重复保存、重复应用，禁止再次领取章节。
10. 验证完成后必须立即结束当前 CLI 回合。禁止再次调用
    `get_next_external_cataloging_chapter`，禁止处理下一章；下一章由司命启动全新的 CLI 回合。
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
- 所有事实、候选和应用操作必须调用 Siming MCP 工具。
- 每个 MCP 调用都必须带 `project_id="{job.project_id}"` 和 `run_id="{agent_run_id}"`。
- 不要创建 candidates.jsonl、临时档案或其他旁路数据文件。

{stage_steps}

## 共享建档提示词
{shared_prompt}

## 输出约束
不要在最终回复里复制章节、完整事实或完整候选。只简短说明本章处理结果；正式数据必须已经通过 MCP 保存。
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
        "第一步必须调用 report_agent_plan，然后严格按附件任务文件执行 MCP 工具链。"
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
    model = effective_local_cli_model(
        config.provider,
        config.default_model or DEFAULT_CLI_MODELS.get(config.provider, config.provider),
    )
    launch = _build_cataloging_cli_launch(
        config=config,
        prompt=_task_prompt(task_file, job, run, chapter, agent_run_id, stage),
        model=model,
        task_file=task_file,
        project_folder=project_folder,
        run=run,
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
    if config.provider == "opencode_cli":
        # OpenCode supports a runtime-only permission override. Keep the managed
        # cataloging child read-only except for the ten Siming cataloging tools.
        env["OPENCODE_PERMISSION"] = opencode_cataloging_permission_env()
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
            poll_seconds=poll_seconds,
            stop_on_permission_request=True,
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

    # CatalogingJob is the authoritative state.  Project it first, then commit
    # the AgentRun and OperationRun sidecars together.  Previously the AgentRun
    # helper committed before finish_operation(), leaving the latter update to
    # be rolled back when this worker session closed.
    refresh_job_progress(db, job)
    if job.agent_run_id:
        agent_run = db.query(AgentRun).filter(AgentRun.id == job.agent_run_id).first()
        if agent_run and agent_run.status != "completed":
            update_run_status(db, agent_run.id, "completed", summary="作品建档完成")
    if job.operation_id:
        completed = int(job.completed_chapters or job.total_chapters or 0)
        finish_operation(
            job.operation_id,
            message=f"作品建档完成，共处理 {completed} 章",
            outcome="completed_with_tools",
            result={
                "summary": f"作品建档完成，共处理 {completed} 章",
                "completed": [f"{completed} 章已完成"],
                "incomplete": [],
            },
            attention={},
            db=db,
        )
        unregister_operation_actions(job.operation_id)
    commit_session(db)


async def _coordinate_cataloging(job_id: str, provider: str) -> None:
    no_save_attempts: dict[str, int] = {}
    try:
        while True:
            db = SessionLocal()
            try:
                job = db.query(CatalogingJob).filter(CatalogingJob.id == job_id).first()
                if not job:
                    return
                if job.status in _TERMINAL_JOBS:
                    if job.status == "completed":
                        _finalize_completed_sidecars(db, job)
                    else:
                        refresh_job_progress(db, job)
                        commit_session(db)
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
                    job.status = "paused_on_failure"
                    job.blocked_chapter_id = run.chapter_id
                    job.error = run.error
                    refresh_job_progress(db, job)
                    commit_session(db)
                    update_run_status(db, agent_run.id, "failed", summary=run.error or "当前章节建档失败")
                    return
                if run.status == "awaiting_confirmation" and job.execution_mode == "manual":
                    job.status = "waiting_confirmation"
                    job.blocked_chapter_id = run.chapter_id
                    agent_run.status = "waiting_confirmation"
                    agent_run.current_step = f"等待确认：第 {run.chapter_order + 1} 章"
                    commit_session(db)
                    return
                project = db.query(Project).filter(Project.id == job.project_id).first()
                chapter = db.query(Chapter).filter(Chapter.id == run.chapter_id).first()
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
                handle_cli_turn_exception(
                    job_id=job_id,
                    chapter_run_id=run_snapshot.id,
                    agent_run_id=agent_run_id,
                    stage=stage,
                    exc=exc,
                    session_factory=SessionLocal,
                )
                return

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
            if job and job.status not in _TERMINAL_JOBS:
                job.status = "paused_on_failure"
                job.error = str(exc)
                refresh_job_progress(db, job)
                commit_session(db)
                if job.agent_run_id:
                    add_event(db, job.agent_run_id, "error", status="error", message=str(exc))
        finally:
            db.close()
    finally:
        _COORDINATORS.pop(job_id, None)
