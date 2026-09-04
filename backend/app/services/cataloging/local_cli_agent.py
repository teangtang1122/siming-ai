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
    CLITurnTerminal,
    CLIQuotaLimitError,
    LocalCLIAdapter,
    communicate_with_cli_quota_detection,
    detect_cli_quota_error,
    effective_local_cli_model,
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
from app.services.cataloging.candidate_io import candidate_to_dict
from app.services.cataloging.fact_store import fact_to_dict
from app.services.cataloging.job_control import complete_cataloging_job, refresh_job_progress
from app.services.cataloging.local_cli_mcp import (
    opencode_cataloging_permission_env,
)
from app.services.cataloging.local_cli_result import (
    agent_tool_event_count,
    handle_cli_turn_exception,
    handle_cli_turn_result,
)
from app.services.cataloging.orchestrator import job_to_dict, run_to_dict, sse_event
from app.services.external_agent.run_service import add_event, create_run, update_run_status
from app.services.tool_category_state import (
    activate_tool_categories,
    create_tool_category_state,
    read_tool_category_audits,
    read_tool_category_state,
    remove_tool_category_state,
)
from app.services.operation_runtime import (
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
    from app.services.cataloging.job_control import first_retryable_run, refresh_job_progress, reset_run_for_retry

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
            "message": "本机 CLI 建档任务状态已加载；实际工具执行进度以本轮回执为准",
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
    return "facts"


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
        if stage == "facts":
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
   工具参数 `facts` 必须直接传原生 JSON 数组，数组元素直接传对象；禁止先序列化成 JSON 字符串。
5. 调用 `verify_external_cataloging_progress`，然后结束本轮。
6. 本轮禁止调用 `save_external_cataloging_candidates`、`apply_pending_cataloging`，
   禁止处理下一章；候选阶段必须由司命启动下一次 CLI 回合。
"""
        else:
            phase = "candidates"
            chapter_outline_type = (
                "outline_update" if chapter.outline_node_id else "outline_create"
            )
            chapter_outline_target = (
                f'，并逐字携带 id="{chapter.outline_node_id}"'
                if chapter.outline_node_id
                else ""
            )
            fact_steps = """
3. 本章事实已经保存。调用 `report_agent_progress` 说明正在恢复第二阶段。
4. 调用 `list_cataloging_facts`，使用本任务中的 chapter_run_id；
   has_more=true 时逐页使用 next_arguments，读完全部事实后再结合相关档案生成候选。
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
6. 直接读取本作品镜像中与事实有关的角色、世界观、大纲文件，合并旧信息后分小批保存候选。
7. 读取每次保存返回值；不完整时只补齐 missing_required_items；若清单漏项，可单独重发一条 chapter_summary 作幂等增补；若清单误把同一身份的别名或近义标题列成多个实体，则单独重发一条带 coverage_manifest_mode="replace" 的 chapter_summary，并给出五个字段齐全的纠正清单；若章节关联只缺少项目，可重发聚合 chapter_link 增补同一条记录；若既有 chapter_link 含清单外别名、误称或错误端点，则单独重发一条带 chapter_link_mode="replace" 的 chapter_link，并完整提供 characters、worldbuilding_titles、locations、items、events 五个数组；不得重发章级大纲；auto_applied=true 时禁止再次 save/apply；等待确认时立即停止。
8. 仅在 auto_applied=true 后调用一次 `verify_external_cataloging_progress`，然后结束本轮。
9. 验证完成后必须立即结束当前 CLI 回合。禁止重复保存、重复应用，禁止再次领取章节。
"""
            managed_override = f"""
## 司命托管候选事务约束（优先于共享提示词）
1. `candidates` 必须是原生 JSON 数组，不得传 JSON 字符串或聚合包装对象。每次调用最多 3 个候选；首次调用必须恰好 2 个并依次为：
   - 一条完整、实质性的 `chapter_summary`；
   - 一条 node_type="chapter" 的 `{chapter_outline_type}`{chapter_outline_target}。
   首次不得夹带其他候选；后续不得重复章级大纲。只有 missing_required_items 明确要求修正 coverage_manifest 时，才可单独重发一条 chapter_summary，系统会更新同一张摘要卡而不是新增重复卡。漏项直接增补；若误列别名或近义标题，设置 coverage_manifest_mode="replace" 并提交完整的 scene_count、characters、worldbuilding、relationships、character_profiles，替换操作不得与任何其他候选同批。既有聚合 chapter_link 含清单外别名、误称或错误端点时，单独提交 chapter_link_mode="replace"，并完整给出 characters、worldbuilding_titles、locations、items、events 五个数组；系统替换同一条关联候选，不新增第二条。
2. chapter_summary.coverage_manifest.scene_count 必须逐字采用 `chapter_overview.payload.scenes` 的数组长度，不得按 outline_fact 数量、段落或主观判断重算。section 节点总数必须恰好等于这个 scene_count；多个 outline_fact 属于同一场景时合并进同一个 section。
3. coverage_manifest.characters 只列稳定、可持续识别的人物。未具名岗位、临时称谓或泛指参与者只写进摘要、场景与章节事件，不得创建或更新角色、状态、关系、档案或角色章节关联。`栏目负责人`、`综合科记录人` 这类未具名岗位不是角色卡。
4. 先读当前 worldbuilding 镜像。coverage_manifest.worldbuilding 只使用当前 active 设定的精确标题；UUID 只能放在 `id` 字段，不能当标题；别名、近义词和同一设定的拆分说法不能重复列入。事实中的 canonical_title_hint 是事实标签；应根据编号、正文和现有内容解析到 active 条目的精确 id/title。两者不同时，在承接该事实的世界观候选用 source_fact_titles 列出原事实标签，显式声明映射。已有设定使用精确 id 的 update/timeline，确有全新稳定规则时才 create。
5. 全章只保存一条聚合 `chapter_link`，一次列全稳定角色、世界观标题、章级大纲、地点、物件和事件；不得按角色、设定或事件各建一条 link。characters 中每个角色只出现一次，由你选择一个 appearance_type。
6. character_relationship 仅用于正文明确确认或改变、且会持续影响后文的稳定关系；同一有向角色对只能选择一个当前 relationship_type，不得同时声明近义类型。本章没有这种变化时 relationships=[] 且不生成关系候选。character_profiles 仅列全新角色或本章确有稳定档案变化的角色；普通出场、提及和当前状态变化不要求 character_update。
7. character_state_update 只使用已读角色卡中的稳定主名；只提交本章有依据的变化字段。电话或消息参与者未明示实时地点时省略 current_location，不得把通话另一端的场景地点写给该人物。appearance 或 age 仅在正文明确变化时提交；修改已有值必须附逐字复制当前值的 appearance_before/age_before，以及本章正文逐字摘录的 appearance_evidence/age_evidence，否则省略。items_or_assets 是整字段替换：已有非空值且本章确需更新时，必须用 items_or_assets_before 逐字复制当前完整值，新值也必须逐字包含旧值并在其后追加本章状态；不得把同场其他人物经手的物件归给当前角色。已有角色必须用真实 id 更新，禁止同名 character_create。解决叙事治理项必须带真实 resolves_item_id 或 resolves_dedupe_key；找不到就保留待复核，不得按标题猜测关闭。
8. 每次保存后只根据返回的 missing_required_items 组织下一批，仍然最多 3 个。除用于补充 coverage_manifest 的单条 chapter_summary 或补充遗漏关联的单条聚合 chapter_link 外，已通过候选不得重发；auto_applied=true 后只验证一次并结束。托管自动模式会在候选完整时由工具事务内应用，禁止调用 apply_pending_cataloging。
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

## 工具类别
每个新模型回合最初只有 `set_tool_categories`。先根据本轮任务选择类别，
例如建档工具属于 cataloging，上报计划和进度属于 agent_runtime。
类别切换会立即结束当前模型步骤；下一步骤使用已经开放的工具，不要再次选择相同类别。
下方“立即调用”的业务步骤均在所需类别已开放后执行。

{stage_steps}

## 共享建档提示词
{shared_prompt}

{managed_override}

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
        "首先按当前开放类别调用 set_tool_categories；类别已开放时直接调用 report_agent_plan，"
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
    model = effective_local_cli_model(
        config.provider,
        (job.model.split(":", 1)[1] if job.model and ":" in job.model else job.model)
        or config.default_model or DEFAULT_CLI_MODELS.get(config.provider, config.provider),
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

    category_file = create_tool_category_state()
    try:
        for _step in range(8):
            state = read_tool_category_state(category_file)
            prompt = _task_prompt(task_file, job, run, chapter, agent_run_id, stage)
            prompt += "\n当前已经开放的工具类别：" + json.dumps(
                state["active_categories"], ensure_ascii=False,
            ) + "。已开放时直接执行本阶段业务，不要重复选择相同类别。"
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
                )
            except CLITurnTerminal as exc:
                if not str(exc).startswith("set_tool_categories:"):
                    raise
                result = (0, exc.stdout, exc.stderr)
            latest = read_tool_category_state(category_file)
            if latest["version"] > latest["active_version"]:
                activate_tool_categories(category_file)
                continue
            return result
        raise RuntimeError("建档 Agent 工具类别切换次数达到上限，未完成当前章节")
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
    agent_run_id: str, stage: str, category_file: str,
) -> tuple[int, str, str]:
    """Execute one model step; a committed category change stops its process."""
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
            terminal_probe=LocalCLIAdapter._terminal_turn_probe({
                "local_cli_mcp_authorized": True,
                "local_cli_mcp_tool_category_state_file": category_file,
            }),
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
