"""Native tool loop for the chapter cataloging Agent.

Business reads, staging and validation use the same registry as CLI/MCP.
The fixed task supplies four tools directly and owns protocol admission and termination.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ...architecture.tool_spec import ToolInputSchemaValidationError, _validate_exported_schema
from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    OutlineNode,
)
from ...modules.continuity.domain.candidate_contract import (
    CATALOGING_SELECTION_TYPES,
    candidate_record_schema_for_selection,
    candidate_record_schema_for_type,
    candidate_type_selection_schema,
    validate_candidate_fields,
)
from ...modules.model_runtime.application.execution import model_executor
from ...prompts.cataloging_source import get_internal_cataloging_system_prompt
from ..agent_tool_stream import collect_tool_turn
from .constants import CATALOGING_MAX_TOKENS, CATALOGING_TEMPERATURE, CATALOGING_TIMEOUT_SECONDS
from .model_selection import cataloging_extra_body

CATALOGING_AGENT_TOOLS = frozenset(
    {
        "get_next_external_cataloging_chapter",
        "read_cataloging_archive",
        "save_external_cataloging_candidates",
        "list_cataloging_candidates",
    }
)
CATALOGING_SELECT_TYPES_TOOL = "select_cataloging_candidate_types"


def cataloging_selection_tool_schema(allowed_types: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": CATALOGING_SELECT_TYPES_TOOL,
            "description": (
                "选择一个候选类型，下一步提供该类型的严格字段格式。"
                "同类可一次或分批提交，格式保持到你重新选择类型。"
                "章级大纲选 *_chapter，场景小节选 *_section；"
                "小节必须明确 scene_number。摘要已保存后，需要新增或修正候选时调用此工具。"
            ),
            "parameters": candidate_type_selection_schema(allowed_types),
        },
    }


def _needs_chapter_summary(db: Session, run: CatalogingChapterRun) -> bool:
    """Use persisted, structurally valid state to select the first write shape."""
    from .candidate_io import candidate_payload
    from .plan_contract import validate_plan_references

    summaries = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.item_type == "chapter_summary",
        CatalogingCandidate.status != "rejected",
    ).all()
    if len(summaries) != 1:
        return True
    payload = candidate_payload(summaries[0])
    try:
        validate_candidate_fields("chapter_summary", payload)
        validate_plan_references(
            db, run.project_id, run, {"item_type": "chapter_summary", "payload": payload}
        )
    except ValueError:
        return True
    return False


def _selection_context(
    db: Session, run: CatalogingChapterRun,
) -> tuple[list[str], dict[str, list[str]], int | None, dict[str, int], dict[str, list[str]]]:
    """Scope outline IDs to this saved chapter; prose never selects them here."""
    from .candidate_io import candidate_payload
    from .scene_contract import plan_scene_count

    chapter = db.get(Chapter, run.chapter_id)
    linked_id = chapter.outline_node_id if chapter is not None else None
    linked = db.get(OutlineNode, linked_id) if linked_id else None
    if linked is not None and (linked.project_id != run.project_id or linked.node_type != "chapter"):
        linked = None
    sections = []
    if linked is not None:
        sections = [row.id for row in db.query(OutlineNode).filter(
            OutlineNode.project_id == run.project_id,
            OutlineNode.parent_id == linked.id,
            OutlineNode.node_type == "section",
        ).all() if row.source_chapter_id in (None, run.chapter_id)]
    choices = list(CATALOGING_SELECTION_TYPES)
    choices.remove("outline_create_chapter" if linked is not None else "outline_update_chapter")
    if not sections:
        choices.remove("outline_update_section")
    summary_row = db.query(CatalogingCandidate).filter(
        CatalogingCandidate.chapter_run_id == run.id,
        CatalogingCandidate.item_type == "chapter_summary",
        CatalogingCandidate.status != "rejected",
    ).one()
    summary = candidate_payload(summary_row)
    characters = summary["character_bindings"]
    worldbuilding = summary["worldbuilding_bindings"]
    relationships = summary["coverage_manifest"]["relationships"]
    limits = {
        "chapter_summary": 1,
        "outline_create_chapter": 1,
        "outline_update_chapter": 1,
        "character_create": sum(row["decision"] == "new" for row in characters),
        "character_update": sum(row["decision"] == "existing" for row in characters),
        "character_state_update": len(characters),
        "worldbuilding_create": sum(row["decision"] == "new" for row in worldbuilding),
        "worldbuilding_update": sum(row["decision"] == "existing" for row in worldbuilding),
        "character_relationship": len(relationships),
        "chapter_link": 1,
        "scene_outline_replace": 1,
    }
    return choices, {
        "outline_update_chapter": [linked.id] if linked is not None else [],
        "outline_update_section": sections,
        "character_create": [row["id"] for row in characters if row["decision"] == "new"],
        "character_update": [row["id"] for row in characters if row["decision"] == "existing"],
        "character_state_update": [row["id"] for row in characters],
        "worldbuilding_create": [row["id"] for row in worldbuilding if row["decision"] == "new"],
        "worldbuilding_update": [row["id"] for row in worldbuilding if row["decision"] == "existing"],
    }, plan_scene_count(db, run), limits, {
        "characters": [row["name"] for row in characters],
        "worldbuilding": [row["name"] for row in worldbuilding],
    }


def cataloging_agent_tool_schemas(
    *, summary_required: bool, selected_type: str | None = None,
    selection_types: list[str] | None = None,
    target_ids: dict[str, list[str]] | None = None,
    scene_count: int | None = None,
    candidate_limits: dict[str, int] | None = None,
    link_names: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Expose only the one candidate shape selected for this model step."""
    from ..workspace.registry import registry

    schemas = [registry.get_spec(name).openai_schema() for name in sorted(CATALOGING_AGENT_TOOLS)]
    save = next(
        schema for schema in schemas
        if schema["function"]["name"] == "save_external_cataloging_candidates"
    )
    parameters = save["function"]["parameters"]
    candidate_array = parameters["properties"]["candidates"]
    if summary_required or selected_type:
        item_type = "chapter_summary" if summary_required else selected_type
        candidate_array["items"] = (
            candidate_record_schema_for_type("chapter_summary") if summary_required else
            candidate_record_schema_for_selection(
                selected_type,
                target_ids=(target_ids or {}).get(selected_type),
                scene_count=scene_count,
                link_characters=(link_names or {}).get("characters"),
                link_worldbuilding=(link_names or {}).get("worldbuilding"),
            )
        )
        candidate_array["description"] = (
            f"本步骤只提交 {item_type} 类型；同类可一次或分批提交，格式保持到你重新选择类型。"
        )
        limit = (candidate_limits or {}).get(item_type)
        if limit is not None:
            candidate_array["maxItems"] = limit
            candidate_array["description"] += f"本章最多提交 {limit} 条此类型候选。"
        if selected_type in {"outline_create_section", "outline_update_section"} and scene_count is not None:
            candidate_array["maxItems"] = scene_count
            candidate_array["description"] += "每个 scene_number 在本次调用中只出现一次。"
    else:
        # The completed plan can be finalized with an empty candidate array.
        # New candidate types must first be selected by the model itself.
        candidate_array["maxItems"] = 0
        candidate_array["description"] = (
            "若计划已完整，传 [] 并设置 finalize=true；若仍需候选，"
            "先调用 select_cataloging_candidate_types 选择类型。"
        )
        parameters["properties"]["finalize"] = {"type": "boolean", "enum": [True]}
        parameters["required"] = list(dict.fromkeys([*parameters.get("required", []), "finalize"]))
    if not summary_required:
        schemas.append(cataloging_selection_tool_schema(selection_types))
    return schemas


async def _execute_cataloging_call(
    db: Session, job: CatalogingJob, run: CatalogingChapterRun, name: str,
    arguments: dict[str, Any], offered: dict[str, Any],
) -> dict:
    from ..workspace.registry import registry

    try:
        # Binding is authorization, not intent inference. Reject
        # attempts to switch the persisted job/chapter/project.
        for key, expected in (
            ("project_id", job.project_id),
            ("job_id", job.id),
            ("chapter_id", run.chapter_id),
            ("chapter_run_id", run.id),
        ):
            if key in arguments and arguments[key] != expected:
                raise ValueError(f"{key} 不属于当前建档回合")
        _validate_exported_schema(offered[name]["function"]["parameters"], arguments)
        if name == CATALOGING_SELECT_TYPES_TOOL:
            result = {
                "tool": name, "status": "ok",
                "detail": "已准备所选类型的字段格式；同类可分批提交，重新选择才切换格式",
                "data": {"selected_types": [arguments["types"][0]]},
            }
        else:
            registry.get_spec(name).validate_input(arguments)
            result = await registry.get_handler(name)(db, job.project_id, arguments)
    except ToolInputSchemaValidationError as exc:
        result = {
            "tool": name,
            "status": "error",
            "detail": str(exc),
            "data": {"path": list(exc.path), "rule": exc.rule, "expected": exc.expected},
        }
    except (ValueError, TypeError) as exc:
        result = {"tool": name, "status": "error", "detail": str(exc)}
    return result


def _record_text_only_response(
    db: Session, run: CatalogingChapterRun, messages: list[dict], responses: int,
) -> None:
    from ...architecture.uow import commit_session
    from .plan_validation import inspect_complete_plan

    report = inspect_complete_plan(db, run)
    run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
    commit_session(db)
    if responses >= 3:
        detail = (
            "；".join(report["missing_required_items"])
            or "尚未通过工具明确 finalize 完整计划"
        )
        raise ValueError(
            f"建档模型连续三次未继续调用工具；{detail}；候选已保留，未写入正式档案"
        )
    feedback = {
        "status": "error",
        "candidate_set_complete": False,
        "requires_explicit_finalization": True,
        **report,
        "instruction": "系统尚未收到完整计划的成功回执。"
        "校验错误是阻塞项，不能作为警告忽略；"
        "保留现有候选，通过已授权工具修正或补全后显式 finalize。"
        "只有工具返回 candidate_set_complete=true 才算完成，不要仅用文字宣布完成。",
    }
    messages.append({"role": "user", "content": json.dumps(feedback, ensure_ascii=False)})
    run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
    commit_session(db)


def _cataloging_messages(db: Session, job: CatalogingJob, run: CatalogingChapterRun) -> list[dict]:
    from .candidate_retry import candidate_recovery_context

    messages = [
        {"role": "system", "content": get_internal_cataloging_system_prompt()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "为当前已保存章节建档",
                    "project_id": job.project_id,
                    "job_id": job.id,
                    "chapter_id": run.chapter_id,
                    "chapter_run_id": run.id,
                    "chapter_version": run.chapter_version,
                    "resume": candidate_recovery_context(db, run, include_payloads=False),
                },
                ensure_ascii=False,
            ),
        },
    ]
    return messages


def _record_unopened_tool_batch(
    db: Session, run: CatalogingChapterRun, messages: list[dict],
    assistant: dict, error: Any,
) -> None:
    from ...architecture.uow import commit_session

    assistant["tool_calls"] = error.calls
    messages.append(assistant)
    for call in error.calls:
        messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "content": json.dumps(
                error.model_error_result(call["function"]["name"]),
                ensure_ascii=False, separators=(",", ":"),
            ),
        })
    run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
    commit_session(db)



async def run_cataloging_agent(
    db: Session,
    job: CatalogingJob,
    run: CatalogingChapterRun,
    *,
    check_active: Any,
    gateway: Any = None,
):
    from ...architecture.uow import commit_session
    from ..workspace.native_tool_batch import (
        MAX_NATIVE_TOOL_NAME_REJECTIONS,
        NativeToolBatchNotOpen,
        validate_workspace_native_tool_batch,
    )
    from ..workspace.registry import registry

    gateway = gateway or model_executor
    messages = _cataloging_messages(db, job, run)
    failures = 0
    text_only_responses = 0
    tool_name_rejections = 0
    selected_type: str | None = None
    for step in range(48):
        check_active(db, job)
        summary_required = _needs_chapter_summary(db, run)
        if summary_required:
            selected_type = None
        selection_types, target_ids, scene_count, candidate_limits, link_names = (
            _selection_context(db, run) if not summary_required else (None, None, None, None, None)
        )
        schemas = cataloging_agent_tool_schemas(
            summary_required=summary_required,
            selected_type=selected_type,
            selection_types=selection_types,
            target_ids=target_ids,
            scene_count=scene_count,
            candidate_limits=candidate_limits,
            link_names=link_names,
        )
        offered = {schema["function"]["name"]: schema for schema in schemas}
        allowed = frozenset(offered)
        response = await collect_tool_turn(
            gateway,
            messages=messages,
            tools=schemas,
            tool_choice="auto",
            model=job.model,
            temperature=CATALOGING_TEMPERATURE,
            max_tokens=CATALOGING_MAX_TOKENS,
            timeout=CATALOGING_TIMEOUT_SECONDS,
            retry=1,
            extra_body=cataloging_extra_body(job.model),
        )
        check_active(db, job)
        assistant = {
            key: response[key]
            for key in ("content", "reasoning_content", "provider_state")
            if response.get(key)
        }
        assistant["role"] = "assistant"
        try:
            batch = validate_workspace_native_tool_batch(
                response["tool_calls"],
                allowed_tool_names=allowed,
                resolve_tool=registry.get,
                require_initial_controller=False,
            )
        except NativeToolBatchNotOpen as error:
            # Receipt the entire denied batch without executing any prefix.
            # The same model must choose from the actual fixed-task contract.
            tool_name_rejections += 1
            error.recovery_fits = tool_name_rejections < MAX_NATIVE_TOOL_NAME_REJECTIONS
            _record_unopened_tool_batch(db, run, messages, assistant, error)
            unavailable = "、".join(error.details["tools"])
            yield {
                "type": "cataloging_plan_correction", "step": step + 1,
                "status": "error",
                "message": f"工具 {unavailable} 本步骤未开放，整批未执行；已返回可用工具让模型修正",
            }
            if not error.recovery_fits:
                raise ValueError(
                    f"模型累计三次调用本步骤未开放的工具 {unavailable}；整批未执行，候选已保留"
                ) from error
            continue
        if batch.calls:
            assistant["tool_calls"] = list(batch.calls)
        else:
            assistant.setdefault("content", "")
        messages.append(assistant)
        if not batch.calls:
            # Text is not a durable completion receipt. Preserve it and return
            # the actual validation blockers to this same model conversation.
            text_only_responses += 1
            _record_text_only_response(db, run, messages, text_only_responses)

            yield {
                "type": "cataloging_plan_correction",
                "step": step + 1,
                "status": "error",
                "message": "计划尚未通过提交校验，已将具体问题交回模型继续修正",
            }
            continue
        for call in batch.calls:
            name = call["function"]["name"]
            arguments = batch.arguments_by_call_id[call["id"]]
            check_active(db, job)
            result = await _execute_cataloging_call(db, job, run, name, arguments, offered)
            if name == CATALOGING_SELECT_TYPES_TOOL and result.get("status") == "ok":
                selected_type = arguments["types"][0]

            data = result.get("data") or {}
            bad = (
                result.get("status") != "ok"
                or bool(data.get("candidate_errors"))
                or bool(data.get("missing_required_items"))
            )
            # A retained invalid record must not exhaust the correction budget
            # while the model is successfully repairing other records in batches.
            made_progress = int(data.get("candidates_saved") or 0) > 0
            if made_progress:
                failures = 0
            elif bad:
                failures += 1
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(
                        result, ensure_ascii=False, default=str, separators=(",", ":")
                    ),
                }
            )
            run.raw_output = json.dumps(messages[2:], ensure_ascii=False, default=str)
            commit_session(db)
            yield {
                "type": "cataloging_tool_result",
                "tool": name,
                "step": step + 1,
                "message": result.get("detail") or f"建档工具：{name}",
                "status": result.get("status"),
            }
            if data.get("candidate_set_complete"):
                return
            if failures >= 3:
                issues = data.get("candidate_errors") or []
                details = [str(issue.get("message") or issue) for issue in issues]
                details.extend(data.get("missing_required_items") or [])
                detail = "；".join(dict.fromkeys(details))
                raise ValueError("连续三次工具校验失败：" + (detail or str(result.get("detail"))))
    raise ValueError("建档 Agent 已达到本章工具步骤上限；候选已保留，未写入正式档案")
