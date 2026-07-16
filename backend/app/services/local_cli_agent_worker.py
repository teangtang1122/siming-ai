"""Launch local CLI agents as Siming workers.

Unlike the LLM adapter path, this worker does not ask the CLI to return a long
JSON/prose blob through stdout. The CLI receives a small task file path and is
instructed to read project files directly, then write/delete/update only via
Siming MCP tools. Progress is visible through AgentRun events.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import (
    CLIQuotaLimitError,
    DEFAULT_CLI_COMMANDS,
    DEFAULT_CLI_MODELS,
    communicate_with_cli_quota_detection,
    detect_cli_quota_error,
    ensure_opencode_logging_args,
    hidden_subprocess_kwargs,
    parse_cli_launch,
    terminate_cli_process_tree,
)
from app.database.models import APIConfig, AgentRun, Project
from app.database.session import SessionLocal
from app.services.content_store import ensure_project_folder
from app.services.external_agent.run_service import add_event, cancel_run, create_run, update_run_status
from app.services.operation_runtime import register_operation_actions, unregister_operation_actions


_TASKS: dict[str, asyncio.Task] = {}
_PROCESSES: dict[str, asyncio.subprocess.Process] = {}


async def _cancel_local_cli_agent(run_id: str) -> None:
    process = _PROCESSES.get(run_id)
    if process and process.returncode is None:
        await terminate_cli_process_tree(process)
    task = _TASKS.get(run_id)
    if task and not task.done():
        task.cancel()
    db = SessionLocal()
    try:
        cancel_run(db, run_id)
    finally:
        db.close()


def _select_cli_config(db: Session, provider: str | None = None) -> APIConfig | None:
    query = db.query(APIConfig).filter(APIConfig.provider_type == "local_cli")
    if provider:
        return query.filter(APIConfig.provider == provider).first()
    return (
        query.filter(APIConfig.is_global_default == True).first()
        or query.order_by(APIConfig.updated_at.desc()).first()
    )


def _task_prompt(task_file: Path) -> str:
    return (
        "你是 Siming 启动的本机 CLI Agent。请读取这个任务文件并严格执行：\n"
        f"{task_file}\n\n"
        "不要把长正文或大量 JSON 输出到聊天/终端；必须通过任务文件指定的 Siming MCP 工具写入数据和汇报进度。"
    )


def _workflow_section(task_type: str) -> str:
    if task_type == "cataloging":
        return """
## Required Workflow: Cataloging
1. Call `get_mcp_permission_status` and `report_agent_plan`.
2. Call `get_moshu_usage_guide` with `scenario="cataloging_no_api"` and `no_api=true`.
3. Call `get_prompt_pack` with `pack_id="cataloging_external_no_api"`.
4. Call `start_external_cataloging_job`.
5. Process chapters strictly in `chapter_order` with the experimental single-stage flow.
6. For each chapter: `get_next_external_cataloging_chapter(phase="merged", include_content=false)` -> `prepare_task_context(task_type="cataloging", arguments={"chapter_id": ...})` -> read the chapter file and project mirror directly -> `search_task_context` when needed -> `submit_context_evidence` for selected required sources -> `save_external_cataloging_candidates` with `phase="merged"` -> `apply_pending_cataloging` -> `verify_external_cataloging_progress`.
7. Do not call `save_external_cataloging_facts` or `list_cataloging_facts` in this experimental flow.
8. Never call `start_cataloging_job` unless the user explicitly allows Siming internal API usage.
"""
    if task_type == "writing":
        return """
## Required Workflow: Writing
1. Call `get_mcp_permission_status` and `report_agent_plan`.
2. Call `prepare_task_context` with this run_id and use its returned baseline manifest.
3. Use `search_task_context` only for a task-specific gap; direct mirror reads are not evidence.
4. Call `submit_context_evidence` with every selected required source before a formal write.
5. Call `prepare_external_writing_context` with `context_manifest_id` to get the compatible quality prompt wrapper.
6. Read relevant project files directly when useful, but write only through Siming MCP tools.
7. Call `save_external_chapter_draft` with `context_manifest_id` for long chapter text instead of printing it.
8. Call `record_external_quality_review`, then `create_chapter` with `draft_id/content_ref` and `context_manifest_id`.
9. Call `archive_chapter_after_write` with the same manifest and standard candidates for chapter summary, chapter outline, section scene state, character state, worldbuilding, and narrative_state (events, foreshadowing, storyline progress, unresolved actions).
10. Call `get_project_archive_status` before reporting completion.
"""
    return """
## Required Workflow: General Project Work
1. Call `get_mcp_permission_status` and `report_agent_plan`.
2. Read project files directly for context when helpful.
3. Use Siming MCP tools for every write/delete/update.
4. Use `report_agent_progress` at meaningful milestones and `finish_agent_run` at the end.
"""


def write_task_file(
    db: Session,
    project: Project,
    *,
    run_id: str,
    user_request: str,
    task_type: str,
    provider: str,
    context_manifest_id: str | None = None,
) -> Path:
    folder = ensure_project_folder(db, project)
    run_dir = folder / ".siming" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    task_file = run_dir / "task.md"
    text = f"""# Siming Local CLI Agent Task

## Run
- run_id: `{run_id}`
- project_id: `{project.id}`
- project_title: `{project.title}`
- provider: `{provider}`
- task_type: `{task_type}`
- project_folder: `{folder}`
- context_manifest_id: `{context_manifest_id or "prepare-per-task"}`

## User Request
{user_request.strip() or "No user request provided."}

## Data Boundary
- The database is the only authoritative source.
- The project folder is a read-only mirror for context.
- You may read files under `project_folder` directly.
- Direct mirror reads are not auditable evidence for a formal write.
- Do not edit, delete, rename, or create files in canonical folders: `chapters`, `characters`, `worldbuilding`, `outline`, `relationships`.
- Every write/delete/update must use Siming MCP tools with `project_id="{project.id}"`.
- Long text must be stored through Siming tools such as `save_external_chapter_draft`, not printed to stdout.

## Required Telemetry
- First, call `report_agent_plan` with this `run_id`.
- During work, call `report_agent_progress` whenever you start/finish a meaningful step.
- If blocked, call `report_agent_progress` with the blocker, then `finish_agent_run` with a clear summary.
- When complete, call `finish_agent_run`.

{_workflow_section(task_type)}

## Language Rules
- Preserve the source novel language. For Chinese novels, save Chinese names, titles, summaries, aliases, outline nodes, and worldbuilding.
- Do not switch Chinese content to English or pinyin because of terminal encoding.

## Quality Rules
- Use Siming prompt packs and workflow guides instead of guessing tool contracts.
- For chapter writing, use the unified quality prompt returned by Siming.
- For cataloging, section-level outline nodes are required when the chapter contains distinct scenes/beats.
- Post-write archive candidates should include chapter_summary.narrative_state and section outline scene fields when the text contains events, foreshadowing, storyline progress, location/time changes, or unresolved actions.
"""
    task_file.write_text(text, encoding="utf-8", newline="\n")
    return task_file


async def _run_cli_process(
    *,
    run_id: str,
    project_id: str,
    provider: str,
    command: str,
    args: list[str],
    stdin_text: str | None,
    cwd: str,
) -> None:
    db = SessionLocal()
    operation_id: str | None = None
    try:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        operation_id = run.operation_id if run else None
        add_event(
            db,
            run_id,
            "cli_started",
            message=f"Started {provider}",
            payload_json=None,
            model_source=f"{provider}:local_cli",
            tool_mode="siming_mcp_task_file",
            storage_target="database_authoritative",
        )
        env = os.environ.copy()
        env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **hidden_subprocess_kwargs(),
        )
        _PROCESSES[run_id] = proc
        try:
            stdout, stderr = await communicate_with_cli_quota_detection(
                proc,
                input_bytes=stdin_text.encode("utf-8") if stdin_text is not None else None,
                timeout_seconds=None,
                operation_id=operation_id,
            )
        except CLIQuotaLimitError as exc:
            stdout = exc.stdout.encode("utf-8")
            stderr = exc.stderr.encode("utf-8")
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        payload = {
            "returncode": proc.returncode,
            "stdout_tail": out_text[-4000:],
            "stderr_tail": err_text[-4000:],
        }
        quota_error = detect_cli_quota_error(err_text, out_text)
        if quota_error:
            add_event(
                db,
                run_id,
                "error",
                status="error",
                message=quota_error,
                payload_json=__import__("json").dumps(payload, ensure_ascii=False),
                model_source=f"{provider}:local_cli",
                tool_mode="siming_mcp_task_file",
                failure_class="quota_or_rate_limit",
                storage_target="database_authoritative",
                next_action="test_local_cli_or_switch_provider",
            )
            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            if run:
                run.summary = quota_error[:1000]
                db.commit()
            return
        if proc.returncode == 0:
            add_event(
                db,
                run_id,
                "cli_finished",
                message=f"{provider} exited successfully",
                payload_json=__import__("json").dumps(payload, ensure_ascii=False),
                model_source=f"{provider}:local_cli",
                tool_mode="siming_mcp_task_file",
                storage_target="database_authoritative",
                next_action="wait_local_cli_agent_run",
            )
            update_run_status(db, run_id, "completed", summary=f"{provider} completed")
        else:
            add_event(
                db,
                run_id,
                "error",
                status="error",
                message=f"{provider} exited with code {proc.returncode}",
                payload_json=__import__("json").dumps(payload, ensure_ascii=False),
                model_source=f"{provider}:local_cli",
                tool_mode="siming_mcp_task_file",
                storage_target="database_authoritative",
                next_action="open_run_events_and_check_cli_output",
            )
    except Exception as exc:
        add_event(
            db,
            run_id,
            "error",
            status="error",
            message=f"CLI worker failed: {exc}",
            model_source=f"{provider}:local_cli",
            tool_mode="siming_mcp_task_file",
            storage_target="database_authoritative",
            next_action="test_local_cli_or_switch_provider",
        )
    finally:
        _PROCESSES.pop(run_id, None)
        _TASKS.pop(run_id, None)
        if operation_id:
            unregister_operation_actions(operation_id)
        db.close()


def start_local_cli_agent_worker(
    db: Session,
    project_id: str,
    *,
    user_request: str,
    task_type: str = "general",
    provider: str | None = None,
    context_manifest_id: str | None = None,
    context_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"status": "skipped", "detail": "Project not found", "data": None}

    cfg = _select_cli_config(db, provider)
    if not cfg:
        return {
            "status": "skipped",
            "detail": "未找到本机 CLI 模型配置，请先在系统设置中配置任一受支持的本机 Agent CLI",
            "data": None,
        }
    provider = cfg.provider
    command = (cfg.cli_command or DEFAULT_CLI_COMMANDS.get(provider) or "").strip()
    if not command:
        return {"status": "skipped", "detail": f"{provider} 未配置 CLI 命令", "data": None}

    run = create_run(
        db,
        project_id,
        source="internal_cli",
        client_name=provider,
        title=f"{task_type}: {(user_request or '')[:80]}",
    )
    model = cfg.default_model or DEFAULT_CLI_MODELS.get(provider, provider)
    manifest = None
    requested_arguments = dict(context_arguments or {})
    requested_arguments.setdefault("requirements", user_request)
    context_task_type = {"writing": "writing", "cataloging": "cataloging"}.get(task_type, "planning")
    # A cataloging worker without a concrete chapter prepares one governed
    # manifest per chapter after it claims that chapter.  Writing/general
    # workers can establish a baseline before the local CLI is launched.
    needs_baseline_now = task_type != "cataloging" or bool(requested_arguments.get("chapter_id"))
    if needs_baseline_now:
        from app.services.context_orchestrator import ContextOrchestrator

        orchestrator = ContextOrchestrator(db)
        manifest = orchestrator.get_manifest(str(context_manifest_id), project_id) if context_manifest_id else None
        if manifest is None:
            manifest = orchestrator.prepare(
                project_id=project_id,
                task_type=context_task_type,
                model=f"{provider}:{model}",
                execution_route="local_cli_agent",
                arguments=requested_arguments,
                pinned_chunk_ids=requested_arguments.get("pinned_chunk_ids") if isinstance(requested_arguments.get("pinned_chunk_ids"), list) else (),
                pinned_source_ids=requested_arguments.get("pinned_source_ids") if isinstance(requested_arguments.get("pinned_source_ids"), list) else (),
            )
        run.context_manifest_id = manifest.id
        usable, detail = orchestrator.validate(manifest)
        if not usable:
            update_run_status(db, run.id, "waiting_confirmation", summary=detail)
            add_event(
                db,
                run.id,
                "context_blocked",
                status="error",
                message=detail,
                payload_json=__import__("json").dumps({"manifest_id": manifest.id, "status": manifest.status}),
                model_source=f"{provider}:local_cli",
                tool_mode="siming_mcp_task_file",
                storage_target="database_authoritative",
                next_action="review_context_manifest",
            )
            return {
                "status": manifest.status,
                "detail": detail,
                "data": {
                    "run_id": run.id,
                    "provider": provider,
                    "task_type": task_type,
                    "context_manifest_id": manifest.id,
                    "context_manifest": orchestrator.manifest_payload(manifest, include_content=False),
                },
            }
    task_file = write_task_file(
        db,
        project,
        run_id=run.id,
        user_request=user_request,
        task_type=task_type,
        provider=provider,
        context_manifest_id=manifest.id if manifest else None,
    )
    db.commit()

    launch = parse_cli_launch(cfg.cli_args, provider, _task_prompt(task_file), model)
    args = list(launch.args)
    ensure_opencode_logging_args(provider, args)
    task = asyncio.create_task(
        _run_cli_process(
            run_id=run.id,
            project_id=project_id,
            provider=provider,
            command=command,
            args=args,
            stdin_text=launch.stdin_text,
            cwd=str(Path(project.folder_path or task_file.parent).resolve()),
        )
    )
    _TASKS[run.id] = task
    if run.operation_id:
        register_operation_actions(
            run.operation_id,
            cancel=lambda: _cancel_local_cli_agent(run.id),
        )
    return {
        "status": "ok",
        "detail": f"已启动本机 CLI Agent：{provider}",
        "data": {
            "run_id": run.id,
            "provider": provider,
            "task_type": task_type,
            "task_file": str(task_file),
            "project_folder": project.folder_path,
            "context_manifest_id": manifest.id if manifest else None,
        },
    }
