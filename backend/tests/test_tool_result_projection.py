from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.architecture.tool_definition import ToolDef
from app.architecture.tool_result_policy import (
    ModelResultContract,
    ModelResultListProjection,
    ModelResultPolicy,
    ModelResultPreview,
)
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    ToolResultBatchOverCapacity,
    ToolResultOverCapacity,
    ToolResultProjectionError,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
    max_native_tool_transaction_wrapper_tokens,
    model_tool_result_projector,
    sanitize_diagnostic_tool_result,
)
from app.services.creation_agent_native_protocol import safe_creation_tool_result
from tests.tool_budget_helpers import request_budget


def _tool(name: str, contract: ModelResultContract) -> ToolDef:
    return ToolDef(
        name=name,
        description="test",
        input_schema={},
        handler=lambda: None,
        model_result_contract=contract,
    )


def test_inline_bounded_delivers_complete_json_without_field_truncation() -> None:
    tool = _tool(
        "search_test",
        ModelResultContract(
            policy=ModelResultPolicy.INLINE_BOUNDED,
            max_json_bytes=32_000,
        ),
    )
    result = {
        "tool": "search_test",
        "status": "ok",
        "detail": "complete",
        "data": [{"id": "one", "content": "正文" * 2_000, "custom": {"x": 1}}],
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload == result
    assert json.loads(projected.content) == result
    assert projected.full_source_delivered is True


def test_inline_bounded_rejects_over_capacity_instead_of_returning_partial_json() -> None:
    tool = _tool(
        "search_test",
        ModelResultContract(max_json_bytes=120),
    )
    result = {
        "tool": "search_test",
        "status": "ok",
        "detail": "complete",
        "data": [{"id": "one", "content": "x" * 500}],
    }

    with pytest.raises(ToolResultOverCapacity) as error:
        model_tool_result_projector.project(tool, result)

    assert error.value.actual_bytes > error.value.max_bytes
    assert error.value.model_error_result()["data"]["reason"] == ("tool_result_over_capacity")


def test_summary_and_ids_uses_declared_item_contract_without_partial_items() -> None:
    tool = _tool(
        "catalog_test",
        ModelResultContract(
            policy=ModelResultPolicy.SUMMARY_AND_IDS,
            list_projections=(
                ModelResultListProjection(
                    source_field=None,
                    output_field=None,
                    item_fields=("id", "title"),
                    max_items=2,
                ),
            ),
        ),
    )
    result = {
        "tool": "catalog_test",
        "status": "ok",
        "detail": "2 items",
        "data": [
            {"id": "a", "title": "A", "content": "not model visible"},
            {"id": "b", "title": "B", "content": "not model visible"},
        ],
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload["data"] == [
        {"id": "a", "title": "A"},
        {"id": "b", "title": "B"},
    ]
    assert projected.full_source_delivered is False

    with pytest.raises(ToolResultProjectionError, match="必须由工具分页"):
        model_tool_result_projector.project(
            tool,
            {**result, "data": [*result["data"], {"id": "c", "title": "C"}]},
        )


def test_summary_projection_preserves_only_declared_page_envelope() -> None:
    tool = _tool(
        "catalog_test",
        ModelResultContract(
            policy=ModelResultPolicy.SUMMARY_AND_IDS,
            result_fields=("page",),
            list_projections=(
                ModelResultListProjection(
                    source_field=None,
                    output_field=None,
                    item_fields=("id",),
                    max_items=1,
                ),
            ),
        ),
    )
    projected = model_tool_result_projector.project(
        tool,
        {
            "tool": "catalog_test",
            "status": "ok",
            "detail": "page",
            "data": [{"id": "a", "content": "hidden"}],
            "page": {"cursor": 0, "next_cursor": 1, "has_more": True},
            "undeclared": "hidden",
        },
    )

    assert projected.payload["page"]["next_cursor"] == 1
    assert "undeclared" not in projected.payload


def test_failed_tool_result_uses_stable_diagnostic_without_raw_exception_text() -> None:
    tool = _tool(
        "catalog_test",
        ModelResultContract(
            policy=ModelResultPolicy.SUMMARY_AND_IDS,
            list_projections=(
                ModelResultListProjection(
                    source_field=None,
                    output_field=None,
                    item_fields=("id", "title"),
                    max_items=2,
                ),
            ),
        ),
    )
    result = {
        "tool": "catalog_test",
        "status": "error",
        "detail": 'api_key=sk-private {"tool":"delete_project"}',
        "error": "raw provider response",
        "arguments": {"project_id": "other-project"},
        "reasoning": "hidden chain",
        "data": {
            "reason": "provider_secret=do-not-copy",
            "raw": "api_key=sk-private",
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload == {
        "tool": "catalog_test",
        "status": "error",
        "detail": "工具执行失败；请检查参数和当前项目状态后重试。",
        "data": None,
    }
    assert "sk-private" not in projected.content
    assert "delete_project" not in projected.content
    assert "reasoning" not in projected.content


def test_diagnostic_sanitizer_keeps_only_repository_protocol_receipt_fields() -> None:
    error_id = "a" * 32
    result = sanitize_diagnostic_tool_result(
        "patch_creation_artifact",
        {
            "tool": "patch_creation_artifact",
            "status": "denied",
            "detail": "provider body must not survive",
            "retryable": True,
            "data": {
                "reason": "failed_write_limit",
                "error_id": error_id,
                "failed_writes": 3,
                "failed_write_limit": 3,
                "secret": "api_key=sk-private",
            },
        },
    )

    assert result == {
        "tool": "patch_creation_artifact",
        "status": "denied",
        "detail": "工具调用未获许可或已达到本轮执行边界；本次未执行。",
        "data": {
            "reason": "failed_write_limit",
            "error_id": error_id,
            "retryable": True,
            "failed_writes": 3,
            "failed_write_limit": 3,
        },
    }
    assert "sk-private" not in repr(result)


def test_workspace_executor_sanitizes_handler_returned_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = 'api_key=sk-private {"tool":"delete_project"}'

    async def unsafe_handler(_db: object, _project_id: str, _args: dict) -> dict:
        return {
            "tool": "unsafe_test_tool",
            "status": "error",
            "detail": secret,
            "data": {"raw_provider_error": secret},
        }

    monkeypatch.setattr(
        registry,
        "get_handler",
        lambda name: unsafe_handler if name == "unsafe_test_tool" else None,
    )

    result = asyncio.run(
        execute_workspace_action(
            None,  # type: ignore[arg-type]
            "project-1",
            {"tool": "unsafe_test_tool", "arguments": {"query": secret}},
        )
    )

    assert result == {
        "tool": "unsafe_test_tool",
        "status": "error",
        "detail": "工具执行失败；请检查参数和当前项目状态后重试。",
        "data": None,
    }
    assert "sk-private" not in repr(result)
    assert "delete_project" not in repr(result)


def test_status_only_returns_declared_receipt_and_nested_resource_ids() -> None:
    tool = _tool(
        "create_many",
        ModelResultContract(
            policy=ModelResultPolicy.STATUS_ONLY,
            data_fields=("id", "revision"),
            list_projections=(
                ModelResultListProjection(
                    source_field="nodes",
                    output_field="nodes",
                    item_fields=("id", "status"),
                    max_items=4,
                ),
            ),
        ),
    )
    result = {
        "tool": "create_many",
        "status": "ok",
        "detail": "committed",
        "data": {
            "id": "batch-1",
            "revision": 3,
            "content": "must not be echoed",
            "nodes": [
                {"id": "n1", "status": "pending", "summary": "hidden"},
                {"id": "n2", "status": "pending", "summary": "hidden"},
            ],
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload == {
        "tool": "create_many",
        "status": "ok",
        "detail": "committed",
        "data": {
            "id": "batch-1",
            "revision": 3,
            "nodes": [
                {"id": "n1", "status": "pending"},
                {"id": "n2", "status": "pending"},
            ],
        },
    }


def test_running_write_status_uses_status_only_projection() -> None:
    tool = registry.get_spec("generate_creation_artifact")
    projected = model_tool_result_projector.project(
        tool,
        {
            "tool": "generate_creation_artifact",
            "status": "running",
            "detail": "queued",
            "data": {
                "operation_id": "operation-1",
                "run": {"huge": "立项" * 20_000},
            },
        },
    )

    assert projected.payload["status"] == "running"
    assert projected.payload["data"] == {"operation_id": "operation-1"}


@pytest.mark.parametrize("case", json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/fixtures/creation_write_receipts.json")
    .read_text(encoding="utf-8").replace("DOCUMENT_BODY", "完整角色背景。" * 5_000)
))
def test_creation_write_receipts_preserve_metadata_without_echoing_documents(case):
    tool = registry.get_spec(case["tool"])
    raw = {"tool": case["tool"], "status": "ok", "detail": "saved", "data": case["data"]}
    original = json.dumps(raw, ensure_ascii=False)

    projected = model_tool_result_projector.project(tool, raw)

    assert projected.payload == {
        "tool": case["tool"], "status": "ok", "detail": "saved", "data": case["expected_data"],
    }
    assert len(projected.content.encode("utf-8")) <= tool.model_result_contract.max_json_bytes
    assert len(original.encode("utf-8")) > tool.model_result_contract.max_json_bytes
    assert json.dumps(raw, ensure_ascii=False) == original


@pytest.mark.parametrize("name", [
    "patch_creation_entity", "delete_creation_entity", "patch_creation_artifact",
    "undo_creation_artifact", "restore_creation_artifact_version",
])
def test_all_creation_edits_use_the_same_bounded_artifact_receipt(name):
    tool = registry.get_spec(name)
    projected = model_tool_result_projector.project(tool, {
        "tool": name, "status": "ok", "detail": "saved",
        "data": {"artifact": {
            "artifact": "characters", "revision": 9, "status": "generated",
            "data": "背景" * 10_000,
        }},
    })

    assert projected.payload["status"] == "ok"
    assert projected.payload["data"] == {
        "artifact": {"artifact": "characters", "revision": 9, "status": "generated"},
    }


def test_needs_confirmation_status_uses_declared_receipt_projection() -> None:
    tool = registry.get_spec("submit_context_evidence")
    projected = model_tool_result_projector.project(
        tool,
        {
            "tool": "submit_context_evidence",
            "status": "needs_confirmation",
            "detail": "selection not ready",
            "data": {
                "manifest_id": "manifest-1",
                "accepted_count": 0,
                "selection_ready": False,
                "task_context": "不得回灌" * 20_000,
                "rejected": [{"huge": "不得回灌" * 20_000}],
            },
        },
    )

    assert projected.payload["status"] == "needs_confirmation"
    assert projected.payload["data"] == {
        "manifest_id": "manifest-1",
        "accepted_count": 0,
        "selection_ready": False,
    }


def test_artifact_reference_keeps_durable_ref_and_declared_preview() -> None:
    tool = _tool(
        "draft_writer",
        ModelResultContract(
            policy=ModelResultPolicy.ARTIFACT_REFERENCE,
            data_fields=("draft_id", "revision"),
            reference_fields=("draft_id",),
            preview=ModelResultPreview(
                source_field="content",
                output_field="content_preview",
                max_chars=8,
            ),
        ),
    )
    result = {
        "tool": "draft_writer",
        "status": "ok",
        "detail": "draft ready",
        "data": {
            "draft_id": "draft-1",
            "revision": 7,
            "content": "abcdefghijklmnop",
            "internal_context": "never model visible",
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload["data"]["draft_id"] == "draft-1"
    assert projected.payload["data"]["content_preview"] == "abcdefgh"
    assert projected.payload["data"]["content_preview_meta"]["truncated"] is True
    assert len(projected.payload["data"]["content_preview_meta"]["sha256"]) == 64
    assert "content" not in projected.payload["data"]
    assert "internal_context" not in projected.payload["data"]


def test_successful_artifact_result_requires_a_persisted_reference() -> None:
    tool = _tool(
        "draft_writer",
        ModelResultContract(
            policy=ModelResultPolicy.ARTIFACT_REFERENCE,
            reference_fields=("draft_id",),
            preview=ModelResultPreview(
                source_field="content",
                output_field="content_preview",
                max_chars=100,
            ),
        ),
    )

    with pytest.raises(ToolResultProjectionError, match="缺少持久化引用"):
        model_tool_result_projector.project(
            tool,
            {
                "tool": "draft_writer",
                "status": "ok",
                "detail": "not persisted",
                "data": {"content": "orphan draft"},
            },
        )


def test_draft_prerequisite_failure_does_not_require_an_artifact_reference() -> None:
    tool = registry.get("save_external_chapter_draft")
    assert tool is not None
    projected = model_tool_result_projector.project(tool, {
        "tool": tool.name,
        "status": "needs_confirmation",
        "detail": "Prepare task context first and attach its context_manifest_id to the draft.",
        "data": {"context_manifest_id": "unavailable-manifest"},
    })
    assert projected.payload["status"] == "needs_confirmation"
    assert "Prepare task context first" in projected.payload["detail"]
    assert "draft_id" not in (projected.payload.get("data") or {})


def test_registry_declares_authoritative_policies_for_generators_writes_and_searches() -> None:
    assert registry.get_model_result_contract("chapter_writer").policy is (
        ModelResultPolicy.ARTIFACT_REFERENCE
    )
    assert registry.get_model_result_contract("outline_writer").policy is (
        ModelResultPolicy.ARTIFACT_REFERENCE
    )
    assert registry.get_model_result_contract("save_external_chapter_draft").policy is (
        ModelResultPolicy.ARTIFACT_REFERENCE
    )
    assert registry.get_model_result_contract("save_external_outline_draft").policy is (
        ModelResultPolicy.ARTIFACT_REFERENCE
    )
    assert registry.get_model_result_contract("create_character").policy is (
        ModelResultPolicy.STATUS_ONLY
    )
    assert registry.get_model_result_contract("search_chapters").policy is (
        ModelResultPolicy.INLINE_BOUNDED
    )


def test_full_outline_is_durable_while_native_receipt_stays_small() -> None:
    from app.services.workspace.assistant_public_projection import public_tool_log

    nodes = [{"id": f"proposal-{index}", "node_type": "chapter", "title": f"Chapter {index}",
              "summary": "完整规划" * 2000, "planned_summary": "完整规划" * 2000,
              "character_names": ["甲"], "status": "pending"} for index in range(12)]
    result = {"tool": "outline_writer", "status": "ok", "detail": "Draft ready", "data": {
        "draft_id": "draft-1", "project_id": "p1", "draft_status": "pending",
        "nodes": nodes, "design_notes": "完整设计说明" * 1000,
        "chapter_outline_node_ids": [node["id"] for node in nodes],
        "saved_outline_node_ids": [], "next_actions": ["edit", "confirm", "discard"],
    }}
    projected = model_tool_result_projector.project(registry.get("outline_writer"), result)
    assert projected.projected_json_bytes <= 4 * 1024
    assert projected.payload["data"]["nodes_preview"] == [
        {"id": node["id"], "node_type": "chapter"} for node in nodes
    ]
    assert "design_notes" not in projected.payload["data"]
    editor = public_tool_log(result, include_success_data=True)["data"]
    assert editor["nodes"] == nodes
    assert editor["design_notes"] == result["data"]["design_notes"]


def test_cataloging_launch_projection_keeps_idempotent_reuse_receipt() -> None:
    tool = registry.get_spec("start_cataloging_job")
    result = {
        "tool": "start_cataloging_job",
        "status": "ok",
        "detail": "当前章节版本已有建档结果，已复用现有任务",
        "data": {
            "id": "job-1",
            "project_id": "project-1",
            "operation_id": "operation-1",
            "status": "completed",
            "started": False,
            "worker_queued": False,
            "idempotent_reuse": True,
            "already_cataloged_chapter_ids": ["chapter-1"],
            "queued_chapter_ids": [],
            "reused_job_ids": ["job-1"],
            "next_action": "already_cataloged",
            "model": "must-stay-in-audit-only",
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload["data"] == {
        "id": "job-1",
        "project_id": "project-1",
        "operation_id": "operation-1",
        "status": "completed",
        "started": False,
        "worker_queued": False,
        "idempotent_reuse": True,
        "already_cataloged_chapter_ids": ["chapter-1"],
        "queued_chapter_ids": [],
        "reused_job_ids": ["job-1"],
        "next_action": "already_cataloged",
    }
    assert "model" not in projected.payload["data"]


def test_every_registered_write_and_scheduler_has_a_bounded_receipt_projection() -> None:
    violations = [
        tool.name
        for tool in (registry.get(name) for name in registry.all_names())
        if tool.tool_type in {"write", "scheduler"}
        and tool.model_result_contract.policy is not ModelResultPolicy.STATUS_ONLY
        and not (
            tool.ends_agent_turn
            and tool.model_result_contract.policy is ModelResultPolicy.ARTIFACT_REFERENCE
        )
    ]

    assert violations == []


def test_registered_tool_defs_and_specs_share_one_result_contract() -> None:
    mismatches = [
        name
        for name in registry.all_names()
        if registry.get(name).model_result_contract != registry.get_spec(name).model_result_contract
    ]

    assert mismatches == []


def test_registered_search_range_is_delivered_in_full_on_first_projection() -> None:
    tool = registry.get_spec("search_chapters")
    result = {
        "tool": "search_chapters",
        "status": "ok",
        "detail": "one exact result",
        "data": [
            {
                "id": "chapter-1",
                "title": "第一章",
                "content": "正文" * 300,
                "content_range": {
                    "offset_chars": 600,
                    "next_offset_chars": 1_200,
                    "has_more": True,
                },
                "quality_detail": {"score": 0.9},
            }
        ],
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload == result
    assert projected.full_source_delivered is True


def test_tool_spec_frontend_metadata_exposes_model_result_policy() -> None:
    metadata = registry.get_spec("chapter_writer").frontend_metadata()

    assert metadata["model_result_policy"] == "artifact_reference"
    assert metadata["model_result_max_json_bytes"] == 2 * 1024


def test_registered_chapter_writer_projection_never_echoes_full_draft() -> None:
    tool = registry.get_spec("chapter_writer")
    result = {
        "tool": "chapter_writer",
        "status": "ok",
        "detail": "draft ready",
        "data": {
            "draft_id": "draft-1",
            "content_ref": "draft-1",
            "content": "章" * 10_000,
            "title": "下一章",
            "outline_node_id": "outline-1",
            "context_snapshot": {"selected_context": "must stay in audit data"},
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.payload["data"]["draft_id"] == "draft-1"
    assert len(projected.payload["data"]["content_preview"]) == 80
    assert "content" not in projected.payload["data"]
    assert "context_snapshot" not in projected.payload["data"]


def test_result_with_wrong_tool_name_is_rejected_before_model_delivery() -> None:
    tool = registry.get_spec("search_chapters")

    with pytest.raises(ToolResultProjectionError, match="不匹配"):
        model_tool_result_projector.project(
            tool,
            {
                "tool": "create_character",
                "status": "ok",
                "detail": "wrong envelope",
                "data": [],
            },
        )


def _native_calls(names, arguments=None):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"call-{i}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}
        for i, name in enumerate(names)
    ]}


@pytest.mark.parametrize("name", [
    "get_creation_session", "get_creation_snapshot", "list_creation_artifacts",
])
def test_single_creation_index_read_fits_observed_64k_remaining_budget(name):
    tool = registry.get(name)
    payload = _native_calls([name], {"session_id": "session-1"})
    content = json.dumps({"tool": name, "status": "ok", "data": {"revision": 0}})

    with pytest.raises(ToolResultBatchOverCapacity):
        admit_native_assistant_transaction(
            payload, [tool], request_budget=request_budget(17_942),
        )
    required = admit_native_assistant_transaction(
        payload, [tool], request_budget=request_budget(17_942),
        result_contents=(content,),
    )

    assert required <= 17_942
    assert tool.capacity_preflight_safe is True


def test_exact_native_read_budget_counts_the_messages_actually_delivered():
    names = ["get_creation_session", "get_creation_snapshot"]
    payload = _native_calls(names, {"session_id": "session-1"})
    tools = declared_model_results_for_tool_names(names, resolve_tool=registry.get)
    contents = tuple(
        json.dumps({"tool": name, "status": "ok", "data": {"label": '玄幻"新书"'}},
                   ensure_ascii=False, separators=(",", ":"))
        for name in names
    )
    expected_messages = [payload, *(
        {"role": "tool", "tool_call_id": call["id"], "content": content}
        for call, content in zip(payload["tool_calls"], contents, strict=True)
    )]
    expected = len(json.dumps(
        expected_messages, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8"))

    assert admit_native_assistant_transaction(
        payload, tools, request_budget=request_budget(expected),
        result_contents=contents,
    ) == expected
    with pytest.raises(ToolResultBatchOverCapacity):
        admit_native_assistant_transaction(
            payload, tools, request_budget=request_budget(expected - 1),
            result_contents=contents,
        )


def test_only_audited_pure_creation_reads_may_preflight_before_admission():
    assert registry.get("get_creation_artifact").capacity_preflight_safe is True
    assert registry.get("patch_creation_session").capacity_preflight_safe is False
    assert registry.get("list_creation_entities").capacity_preflight_safe is True
    # Dependency graph reads still commit derived state in their handler.
    assert registry.get("get_creation_dependency_graph").capacity_preflight_safe is False


def test_artifact_patch_reserves_its_metadata_receipt_before_writing():
    tool = registry.get("patch_creation_artifact")
    assert tool.model_result_contract.max_json_bytes == 2 * 1024
    assert tool.capacity_preflight_safe is False

    payload = _native_calls(["patch_creation_artifact"], {
        "artifact": "constraints",
        "expected_revision": 7,
        "changes": [{"path": "/genre", "action": "replace", "value": "玄幻"}],
    })
    assert admit_native_assistant_transaction(
        payload, [tool], request_budget=request_budget(5_559),
    ) <= 5_559

    # The committed document and submitted patch are retained by the tool,
    # but only the bounded revision receipt is sent back to the model.
    success = model_tool_result_projector.project(tool, {
        "tool": tool.name, "status": "ok", "detail": "Artifact patched",
        "data": {
            "session_id": "a" * 36, "artifact": "constraints", "revision": 8,
            "changes": [{"value": "正文" * 100_000}],
            "affected_artifacts": ["characters"],
        },
    })
    assert len(success.content.encode("utf-8")) < tool.model_result_contract.max_json_bytes
    assert success.payload["data"] == {
        "session_id": "a" * 36, "artifact": "constraints", "revision": 8,
    }

    diagnostic = model_tool_result_projector.project(tool, {
        "tool": tool.name, "status": "error", "detail": "untrusted",
        "data": {
            "failure_class": "invalid_tool_arguments",
            "path": "$" + ".a" * 127 + ".",
            "rule": "r" * 80,
        },
    })
    assert len(diagnostic.content.encode("utf-8")) < tool.model_result_contract.max_json_bytes


def test_single_session_patch_fits_the_observed_64k_request_remainder():
    tool = registry.get("patch_creation_session")
    assert tool.model_result_contract.max_json_bytes == 2 * 1024
    payload = _native_calls([tool.name], {
        "session_id": "a" * 36,
        "expected_revision": 0,
        "changes": {"form": {"genre": "玄幻"}},
    })
    payload["reasoning_content"] = "The user chose the fantasy genre; save it now."
    required = admit_native_assistant_transaction(
        payload, [tool], request_budget=request_budget(7_034),
    )
    assert required <= 7_034
    projected = model_tool_result_projector.project(tool, {
        "tool": tool.name, "status": "ok", "detail": "Creation session patched",
        "data": {
            "session_id": "a" * 36, "revision": 1, "status": "drafting",
            "current_stage": "constraints", "changed_fields": ["form.genre"],
        },
    })
    assert len(projected.content.encode("utf-8")) < tool.model_result_contract.max_json_bytes
    assert projected.payload["data"]["revision"] == 1


def test_single_tool_capacity_error_does_not_suggest_reducing_parallel_calls():
    error = ToolResultBatchOverCapacity(
        tool_names=("patch_creation_session",), required_tokens=8_692,
        available_tokens=7_034, call_count=1,
    )
    projected = safe_creation_tool_result(
        "patch_creation_session", error.model_error_result("patch_creation_session"),
    )
    assert "缩窄当前步骤开放的工具类别" in projected["detail"]
    assert "并行" not in projected["detail"]


@pytest.mark.parametrize("names", [["search_outline"] * 3, ["list_chapters"] * 4])
def test_incident_batches_fit_the_bound_model_budget(names):
    payload = _native_calls(names, {"limit": 2})
    payload["reasoning_content"] = "思考状态" * 2_000
    tools = declared_model_results_for_tool_names(names, resolve_tool=registry.get)
    original = json.dumps(payload, ensure_ascii=False)
    needed = admit_native_assistant_transaction(payload, tools, request_budget=request_budget())
    assert needed > 16_384
    assert json.dumps(payload, ensure_ascii=False) == original


def test_remaining_budget_controls_admission_and_no_fixed_call_count_limit():
    tool = _tool("tiny", ModelResultContract(max_json_bytes=128))
    payload = _native_calls(["tiny"] * 20)
    assert admit_native_assistant_transaction(payload, [tool] * 20, request_budget=request_budget()) > 20 * 128
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, [tool] * 20, request_budget=request_budget(100))
    assert caught.value.reason == "native_assistant_transaction_over_capacity"
    assert not caught.value.recovery_fits


def test_budget_shortage_preserves_whole_denial_protocol_when_it_fits():
    payload = _native_calls(["search_outline"] * 3)
    tools = declared_model_results_for_tool_names(["search_outline"] * 3, resolve_tool=registry.get)
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, tools, request_budget=request_budget(20_000))
    error = caught.value
    assert error.reason == "tool_result_batch_over_capacity"
    assert error.declared_result_json_bytes == 3 * registry.get(
        "search_outline"
    ).model_result_contract.bytes_for_arguments({})
    assert error.recovery_fits
    assert error.model_error_result("search_outline")["data"]["available_tokens"] == 20_000


@pytest.mark.parametrize("field", ["content", "reasoning_content", "provider_state"])
def test_complete_utf8_assistant_state_is_counted_without_truncation(field):
    payload = _native_calls(["list_chapters"], {"limit": 1})
    payload[field] = [{"opaque": "界" * 6_000}] if field == "provider_state" else "界" * 6_000
    tools = [registry.get("list_chapters")]
    admit_native_assistant_transaction(payload, tools, request_budget=request_budget())
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, tools, request_budget=request_budget(8_000))
    assert caught.value.reason == "native_assistant_transaction_over_capacity"
    assert not caught.value.recovery_fits


@pytest.mark.parametrize("mutation", ["duplicate_id", "wrong_tool", "invalid_json", "non_object"])
def test_invalid_native_protocol_is_rejected_even_with_plenty_of_capacity(mutation):
    payload = _native_calls(["list_chapters"] * 2)
    if mutation == "duplicate_id":
        payload["tool_calls"][1]["id"] = payload["tool_calls"][0]["id"]
    elif mutation == "wrong_tool":
        payload["tool_calls"][0]["function"]["name"] = "write"
    else:
        payload["tool_calls"][0]["function"]["arguments"] = "{" if mutation == "invalid_json" else "[]"
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, [registry.get("list_chapters")] * 2,
                                           request_budget=request_budget())
    assert caught.value.reason == "native_assistant_transaction_invalid"


def test_missing_bound_budget_does_not_silently_fall_back():
    with pytest.raises(ValueError, match="请求预算"):
        admit_native_assistant_transaction(_native_calls(["list_chapters"]),
                                           [registry.get("list_chapters")], request_budget=None)


@pytest.mark.parametrize("tool_name", ["list_chapters", "search_outline_tree"])
def test_smaller_page_has_smaller_enforced_result_ceiling(tool_name):
    tool = registry.get(tool_name)
    contract = tool.model_result_contract
    small = contract.bytes_for_arguments({"limit": 1})
    assert small < contract.bytes_for_arguments({}) <= contract.max_json_bytes
    raw = {"tool": tool_name, "status": "ok", "detail": "", "data": []}
    projected = model_tool_result_projector.project(tool, raw, arguments={"limit": 1})
    assert json.loads(projected.content)["data"] == []
    raw["detail"] = "x" * small
    with pytest.raises(ToolResultOverCapacity):
        model_tool_result_projector.project(tool, raw, arguments={"limit": 1})


def test_targeted_outline_read_defaults_to_one_child_and_fits_observed_local_budget():
    tool = registry.get("search_outline")
    assert tool.model_result_contract.bytes_for_arguments({"summary_chars": 200}) == (
        tool.model_result_contract.bytes_for_arguments({"limit": 1, "summary_chars": 200})
    )
    assert tool.model_result_contract.bytes_for_arguments({"limit": 2, "summary_chars": 200}) > (
        tool.model_result_contract.bytes_for_arguments({"summary_chars": 200})
    )
    payload = _native_calls(["search_outline"], {
        "node_id": "53f62a20-9098-4b9e-8634-3bb3fee92e90", "summary_chars": 200,
    })
    assert admit_native_assistant_transaction(
        payload, [tool], request_budget=request_budget(30_781),
    ) <= 30_781


def test_new_budget_contract_matches_cross_platform_fixture():
    fixture = json.loads((Path(__file__).parents[2] / "contracts/fixtures/conversation-context-v1-interop.json").read_text(encoding="utf-8"))
    budget = fixture["native_tool_budget"]
    assert budget["schema"] == "native_tool_transaction_budget.v3"
    assert budget["next_step_wrapper_tokens"] == max_native_tool_transaction_wrapper_tokens() == 1024
    assert "max_native_assistant_transaction_json_bytes" not in budget
    for tool, page in budget["page_budgets"].items():
        contract = registry.get(tool).model_result_contract
        assert contract.bytes_for_arguments({}) == contract.bytes_for_arguments({
            "limit": page.get("default_items", page["max_items"]),
        })
        for count in (1, page["max_items"]):
            text_bytes = 6 * page.get("default_text_chars", 0) * max(
                page.get("min_text_fields", 0), count * page.get("text_fields_per_item", 0))
            assert contract.bytes_for_arguments({"limit": count}) == page["base_json_bytes"] + page["item_json_bytes"] * count + text_bytes
        assert contract.max_json_bytes == budget["standalone_result_json_bytes_by_tool"][tool]


def test_external_writing_context_accepts_one_full_chinese_context_page() -> None:
    tool = registry.get("prepare_external_writing_context")
    result = {
        "tool": tool.name,
        "status": "ok",
        "detail": "writing context prepared",
        "data": {
            "context_manifest_id": "manifest-1",
            "context_page": {
                "text": "当前设定与章节锚点。" * 900,
                "cursor": 0,
                "next_cursor": 9_000,
                "has_more": True,
                "total_chars": 18_000,
                "sha256": "a" * 64,
            },
            "next_tool_suggestions": [{"tool": "prepare_external_writing_context"}],
        },
    }

    projected = model_tool_result_projector.project(tool, result)

    assert projected.full_source_delivered is True
    assert 16 * 1024 < projected.projected_json_bytes <= 32 * 1024


def test_search_and_read_schemas_declare_real_page_or_range_boundaries() -> None:
    bounded_limits = []
    for name in registry.all_names():
        if not name.startswith(("search_", "read_")):
            continue
        schema = registry.get(name).input_schema
        if "limit" in schema:
            bounded_limits.append(name)
            assert int(schema["limit"].get("maximum") or 0) > 0, name

    assert set(bounded_limits) >= {
        "search_characters",
        "search_chapters",
        "search_outline",
        "search_outline_tree",
        "search_worldbuilding",
        "search_relationships",
        "search_project_files",
        "search_context",
        "search_task_context",
    }
    for name, offset_field, range_field in (
        ("read_project_file", "offset_chars", "max_chars"),
        ("read_imported_file", "offset_chars", "max_size"),
        ("search_chapters", "content_offset_chars", "content_chars"),
        ("search_worldbuilding", "content_offset_chars", "content_chars"),
    ):
        schema = registry.get(name).input_schema
        assert offset_field in schema, name
        assert int(schema[range_field].get("maximum") or 0) > 0, name

    task_search = registry.get("search_task_context").input_schema
    assert task_search["limit"]["default"] == 10
    assert task_search["limit"]["maximum"] == 10
    assert task_search["cursor"]["default"] == 0
    assert task_search["cursor"]["maximum"] == 20


@pytest.mark.parametrize(("name", "status", "receipt"), [
    ("save_external_cataloging_candidates", "skipped", {
        "candidate_errors": [{"message": "items_or_assets 必须是字符串"}],
        "candidate_set_complete": False,
        "next_tool": "save_external_cataloging_candidates",
    }),
    ("save_external_cataloging_candidates", "ok", {
        "candidate_set_complete": False,
        "missing_required_items": ["character_state_update for declared characters (0/1)"],
        "candidates_saved": 2, "chapter_run_status": "extracting",
        "auto_applied": False, "next_tool": "save_external_cataloging_candidates",
    }),
    ("save_external_cataloging_candidates", "ok", {
        "candidate_set_complete": True, "missing_required_items": [],
        "chapter_run_status": "completed", "auto_applied": True,
        "next_tool": "verify_external_cataloging_progress",
    }),
])
def test_cataloging_projection_preserves_actionable_receipts_without_prose(name, status, receipt):
    tool = registry.get(name)
    result = {"tool": name, "status": status, "detail": "canonical receipt", "data": {
        "job_id": "job", "project_id": "project", "chapter_id": "chapter", **receipt,
        "content": "完整正文不应在写入回执中重复" * 10000,
    }}
    projected = model_tool_result_projector.project(tool, result)
    assert projected.payload["data"] == {
        "job_id": "job", "project_id": "project", "chapter_id": "chapter", **receipt,
    }
    assert projected.projected_json_bytes <= tool.model_result_contract.max_json_bytes


def test_cataloging_generation_schema_keeps_strict_plan_validation():
    tool = registry.get_spec("save_external_cataloging_candidates")
    generation = tool.parameters_schema()["properties"]["candidates"]["items"]
    assert "anyOf" not in generation
    assert generation["required"] == ["type"]
    variants = tool.input_validation_schema_override["properties"]["candidates"]["items"]["anyOf"]
    summary = next(v for v in variants if v["properties"]["type"]["enum"] == ["chapter_summary"])
    assert {"character_bindings", "worldbuilding_bindings", "scenes"} <= set(summary["required"])
    assert summary["additionalProperties"] is False
