"""Native tool loop for the chapter cataloging Agent.

Business reads, staging and validation use the same registry as CLI/MCP.
The loop owns protocol admission, category selection and bounded termination.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...architecture.tool_categories import (TOOL_CATEGORY_CONTROLLER, normalize_tool_categories,
                                             tool_category_controller_schema, tool_names_for_categories)
from ...architecture.tool_spec import ToolInputSchemaValidationError
from ...database.models import CatalogingJob, CatalogingChapterRun
from ...modules.model_runtime.application.execution import model_executor
from ...prompts.cataloging_source import get_internal_cataloging_system_prompt
from ..agent_tool_stream import collect_tool_turn
from .constants import CATALOGING_MAX_TOKENS, CATALOGING_TIMEOUT_SECONDS, CATALOGING_TEMPERATURE
from .model_selection import cataloging_extra_body

CATALOGING_AGENT_TOOLS = frozenset({"get_next_external_cataloging_chapter", "read_cataloging_archive",
                                    "save_external_cataloging_candidates", "list_cataloging_candidates"})


async def run_cataloging_agent(db: Session, job: CatalogingJob, run: CatalogingChapterRun,
                              *, check_active: Any, gateway: Any = None):
    from ..workspace.registry import registry
    from ..workspace.native_tool_batch import validate_workspace_native_tool_batch
    from .candidate_retry import candidate_recovery_context
    from .plan_validation import inspect_complete_plan
    from ...architecture.uow import commit_session

    gateway = gateway or model_executor
    messages = [{"role": "system", "content": get_internal_cataloging_system_prompt()},
                {"role": "user", "content": json.dumps({"task": "为当前已保存章节建档",
                    "project_id": job.project_id, "job_id": job.id, "chapter_id": run.chapter_id,
                    "chapter_run_id": run.id, "chapter_version": run.chapter_version,
                    "resume": candidate_recovery_context(db, run, include_payloads=False)}, ensure_ascii=False)}]
    categories: tuple[str, ...] = ()
    category_selected = False
    failures = 0
    text_only_responses = 0
    for step in range(48):
        check_active(db, job)
        allowed = CATALOGING_AGENT_TOOLS & tool_names_for_categories(categories)
        schemas = [tool_category_controller_schema(), *[
            registry.get_spec(name).openai_schema() for name in sorted(allowed)
            if registry.get_spec(name) is not None]]
        response = await collect_tool_turn(gateway, messages=messages, tools=schemas, tool_choice="auto",
            model=job.model, temperature=CATALOGING_TEMPERATURE, max_tokens=CATALOGING_MAX_TOKENS,
            timeout=CATALOGING_TIMEOUT_SECONDS, retry=1, extra_body=cataloging_extra_body(job.model))
        check_active(db, job)
        batch = validate_workspace_native_tool_batch(response["tool_calls"],
            allowed_tool_names=allowed | {TOOL_CATEGORY_CONTROLLER}, resolve_tool=registry.get,
            require_initial_controller=not category_selected)
        assistant = {key: response[key] for key in ("content", "reasoning_content", "provider_state") if response.get(key)}
        assistant["role"] = "assistant"
        if batch.calls:
            assistant["tool_calls"] = list(batch.calls)
        else:
            assistant.setdefault("content", "")
        messages.append(assistant)
        if not batch.calls:
            # Text is not a durable completion receipt. Preserve it and return
            # the actual validation blockers to this same model conversation.
            text_only_responses += 1
            report = inspect_complete_plan(db, run)
            run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
            commit_session(db)
            if text_only_responses >= 3:
                detail = "；".join(report["missing_required_items"]) or "尚未通过工具明确 finalize 完整计划"
                raise ValueError(f"建档模型连续三次未继续调用工具；{detail}；候选已保留，未写入正式档案")
            feedback = {
                "status": "error", "candidate_set_complete": False,
                "requires_explicit_finalization": True, **report,
                "instruction": "系统尚未收到完整计划的成功回执。校验错误是阻塞项，不能作为警告忽略；"
                    "保留现有候选，通过已授权工具修正或补全后显式 finalize。"
                    "只有工具返回 candidate_set_complete=true 才算完成，不要仅用文字宣布完成。",
            }
            messages.append({"role": "user", "content": json.dumps(feedback, ensure_ascii=False)})
            run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
            commit_session(db)
            yield {"type": "cataloging_plan_correction", "step": step + 1, "status": "error",
                   "message": "计划尚未通过提交校验，已将具体问题交回模型继续修正"}
            continue
        for call in batch.calls:
            name = call["function"]["name"]
            arguments = batch.arguments_by_call_id[call["id"]]
            check_active(db, job)
            try:
                if name == TOOL_CATEGORY_CONTROLLER:
                    categories = normalize_tool_categories(arguments.get("enabled_categories"))
                    category_selected = True
                    result = {"status": "ok", "enabled_categories": list(categories), "step_complete": True}
                else:
                    # Binding is authorization, not intent inference. Reject
                    # attempts to switch the persisted job/chapter/project.
                    for key, expected in (("project_id", job.project_id), ("job_id", job.id),
                                          ("chapter_id", run.chapter_id), ("chapter_run_id", run.id)):
                        if key in arguments and arguments[key] != expected:
                            raise ValueError(f"{key} 不属于当前建档回合")
                    registry.get_spec(name).validate_input(arguments)
                    result = await registry.get_handler(name)(db, job.project_id, arguments)
            except ToolInputSchemaValidationError as exc:
                result = {"tool": name, "status": "error", "detail": str(exc),
                          "data": {"path": list(exc.path), "rule": exc.rule, "expected": exc.expected}}
            except (ValueError, TypeError) as exc:
                result = {"tool": name, "status": "error", "detail": str(exc)}
            data = result.get("data") or {}
            bad = result.get("status") != "ok" or bool(data.get("candidate_errors"))
            # A retained invalid record must not exhaust the correction budget
            # while the model is successfully repairing other records in batches.
            made_progress = int(data.get("candidates_saved") or 0) > 0
            failures = failures + 1 if bad and not made_progress else 0
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))})
            run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
            commit_session(db)
            yield {"type": "cataloging_tool_result", "tool": name, "step": step + 1,
                   "message": result.get("detail") or f"建档工具：{name}", "status": result.get("status")}
            if data.get("candidate_set_complete"):
                return
            if failures >= 3:
                issues = data.get("candidate_errors") or []
                details = [str(issue.get("message") or issue) for issue in issues]
                details.extend(data.get("missing_required_items") or [])
                detail = "；".join(dict.fromkeys(details))
                raise ValueError("连续三次工具校验失败：" + (detail or str(result.get("detail"))))
    raise ValueError("建档 Agent 已达到本章工具步骤上限；候选已保留，未写入正式档案")
