from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.architecture.tool_categories import tool_category_controller_schema
from app.architecture.tool_definition import ToolDef
from app.architecture.tool_result_policy import (
    DEFAULT_MODEL_RESULT_CONTRACT,
    ModelResultContract,
    ModelResultListProjection,
    ModelResultPolicy,
    ModelResultPreview,
)
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
    MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
    ToolResultBatchOverCapacity,
    ToolResultOverCapacity,
    ToolResultProjectionError,
    admit_model_tool_result_batch,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
    max_model_visible_result_tokens_for_open_tool_schemas,
    max_native_tool_transaction_wrapper_tokens,
    model_tool_result_projector,
    sanitize_diagnostic_tool_result,
)


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


def test_short_draft_projection_keeps_deterministic_retry_receipt() -> None:
    tool = registry.get("save_external_chapter_draft")
    assert tool is not None
    projected = model_tool_result_projector.project(tool, {
        "tool": tool.name,
        "status": "needs_confirmation",
        "detail": "正文低于硬下限；未保存草稿。",
        "data": {
            "reason_code": "draft_below_minimum",
            "context_manifest_id": "manifest-1",
            "actual_han_characters": 3_202,
            "minimum_han_characters": 3_400,
            "missing_han_characters": 198,
            "draft_stored": False,
            "context_selection_token_consumed": False,
            "context_selection_token": "must-not-be-projected",
        },
    })

    assert projected.payload["data"] == {
        "context_manifest_id": "manifest-1",
        "reason_code": "draft_below_minimum",
        "actual_han_characters": 3_202,
        "minimum_han_characters": 3_400,
        "missing_han_characters": 198,
        "draft_stored": False,
        "context_selection_token_consumed": False,
    }


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
    assert metadata["model_result_max_json_bytes"] == 16 * 1024


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
    assert len(projected.payload["data"]["content_preview"]) == 1_200
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


def test_open_tool_reserve_and_batch_admission_share_one_hard_boundary() -> None:
    schemas = [
        tool_category_controller_schema(),
        registry.get_spec("search_chapters").openai_schema(),
    ]
    reserve = max_model_visible_result_tokens_for_open_tool_schemas(
        schemas,
        resolve_tool=registry.get,
    )
    assert reserve == MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES

    one_search = declared_model_results_for_tool_names(
        ["search_chapters"],
        resolve_tool=registry.get,
    )
    assert admit_model_tool_result_batch(one_search) == 16 * 1024

    two_searches = declared_model_results_for_tool_names(
        ["search_chapters", "search_outline"],
        resolve_tool=registry.get,
    )
    assert admit_model_tool_result_batch(two_searches) == reserve

    three_searches = declared_model_results_for_tool_names(
        ["search_chapters", "search_outline", "search_characters"],
        resolve_tool=registry.get,
    )
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_model_tool_result_batch(three_searches)
    assert caught.value.model_error_result("search_chapters")["data"]["reason"] == (
        "tool_result_batch_over_capacity"
    )


def test_native_batch_admission_has_no_fixed_call_count_limit() -> None:
    tools = tuple(
        _tool(
            f"tiny_status_{index}",
            ModelResultContract(
                policy=ModelResultPolicy.STATUS_ONLY,
                max_json_bytes=128,
            ),
        )
        for index in range(20)
    )
    assistant_payload = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"call-{index}",
                "type": "function",
                "function": {"name": tool.name, "arguments": "{}"},
            }
            for index, tool in enumerate(tools)
        ],
    }

    assert admit_model_tool_result_batch(tools) == 20 * 128
    assert admit_native_assistant_transaction(assistant_payload, tools) > 20 * 128


def _assistant_payload(arguments: str = "{}", **extra: object) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_chapters", "arguments": arguments},
            }
        ],
        **extra,
    }


def test_exact_native_assistant_admission_counts_utf8_reasoning_and_provider_state() -> None:
    tools = declared_model_results_for_tool_names(
        ["search_chapters"],
        resolve_tool=registry.get,
    )
    admitted = admit_native_assistant_transaction(
        _assistant_payload(
            json.dumps({"query": "城门"}, ensure_ascii=False),
            reasoning_content="需要查询章节",
            provider_state=[{"opaque": "状态"}],
        ),
        tools,
    )
    assert admitted <= (
        max_native_tool_transaction_wrapper_tokens()
        + MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    )


@pytest.mark.parametrize(
    "payload",
    [
        _assistant_payload(json.dumps({"query": "界" * 6_000}, ensure_ascii=False)),
        _assistant_payload(reasoning_content="界" * 6_000),
        _assistant_payload(provider_state=[{"opaque": "界" * 6_000}]),
    ],
)
def test_large_utf8_native_assistant_state_is_rejected_before_handlers(
    payload: dict[str, object],
) -> None:
    tools = declared_model_results_for_tool_names(
        ["search_chapters"],
        resolve_tool=registry.get,
    )
    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, tools)
    assert caught.value.reason == "native_assistant_transaction_over_capacity"


def test_invalid_native_assistant_state_has_a_stable_protocol_reason() -> None:
    tools = declared_model_results_for_tool_names(
        ["search_chapters"],
        resolve_tool=registry.get,
    )
    payload = _assistant_payload(provider_state={"not_json": {"a", "set"}})

    with pytest.raises(ToolResultBatchOverCapacity) as caught:
        admit_native_assistant_transaction(payload, tools)

    assert caught.value.reason == "native_assistant_transaction_invalid"
    assert caught.value.model_error_result("search_chapters")["data"]["reason"] == (
        "native_assistant_transaction_invalid"
    )

    mismatched = _assistant_payload()
    mismatched["tool_calls"][0]["function"]["name"] = "search_outline"
    with pytest.raises(ToolResultBatchOverCapacity) as mismatched_error:
        admit_native_assistant_transaction(mismatched, tools)
    assert mismatched_error.value.reason == "native_assistant_transaction_invalid"


def test_native_tool_budget_constants_match_cross_platform_fixture() -> None:
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "fixtures"
            / "conversation-context-v1-interop.json"
        ).read_text(encoding="utf-8")
    )["native_tool_budget"]

    assert fixture["max_native_assistant_transaction_json_bytes"] == (
        MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES
    )
    assert fixture["max_model_visible_tool_result_batch_json_bytes"] == (
        MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    )
    assert fixture["schema"] == "native_tool_transaction_budget.v2"
    assert "max_native_tool_calls_per_step" not in fixture
    assert fixture["next_step_wrapper_tokens"] == (max_native_tool_transaction_wrapper_tokens())
    assert fixture["max_projected_transaction_growth_tokens"] == (
        max_native_tool_transaction_wrapper_tokens()
        + MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    )
    assert fixture["errors"] == {
        "assistant_over_capacity": "native_assistant_transaction_over_capacity",
        "assistant_invalid": "native_assistant_transaction_invalid",
        "result_batch_over_capacity": "tool_result_batch_over_capacity",
    }
    standalone_contracts = fixture["standalone_result_json_bytes_by_tool"]
    assert standalone_contracts["set_tool_categories"] == 4 * 1024
    assert {
        name: registry.get_model_result_contract(name).max_json_bytes
        for name in standalone_contracts
        if name != "set_tool_categories"
    } == {
        name: max_bytes
        for name, max_bytes in standalone_contracts.items()
        if name != "set_tool_categories"
    }


def test_next_step_reserve_counts_independent_native_replay_objects_once() -> None:
    # The first 16 KiB is the exact assistant message (content, calls,
    # reasoning and provider state). The second bounds the aggregate result
    # message wrappers using metadata already present in that assistant JSON;
    # declared result contents are the separate 32 KiB batch below.
    wrapper = max_native_tool_transaction_wrapper_tokens()
    assert wrapper == 2 * MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES
    assert wrapper + MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES == 65_536


def test_default_contract_and_common_read_pairs_do_not_consume_the_whole_batch_alone() -> None:
    assert DEFAULT_MODEL_RESULT_CONTRACT.max_json_bytes == 16 * 1024

    for names in (
        ("search_chapters", "search_outline"),
        ("search_characters", "search_worldbuilding"),
        ("list_characters", "search_chapters"),
    ):
        tools = declared_model_results_for_tool_names(names, resolve_tool=registry.get)
        assert admit_model_tool_result_batch(tools) <= (
            MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
        ), names

    evidence_page = declared_model_results_for_tool_names(
        ("search_task_context",),
        resolve_tool=registry.get,
    )
    assert admit_model_tool_result_batch(evidence_page) == (
        MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    )
    with pytest.raises(ToolResultBatchOverCapacity):
        admit_model_tool_result_batch(
            declared_model_results_for_tool_names(
                ("search_task_context", "search_context"),
                resolve_tool=registry.get,
            )
        )


def test_all_registered_results_fit_the_admitted_single_result_boundary() -> None:
    violations = [
        tool.name
        for tool in (registry.get(name) for name in registry.all_names())
        if tool.model_result_contract.max_json_bytes
        > MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    ]
    assert violations == []
    large_single_step_reads = {
        tool.name
        for tool in (registry.get(name) for name in registry.all_names())
        if tool.model_result_contract.max_json_bytes
        == MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES
    }
    assert large_single_step_reads == {
        "read_project_file",
        "prepare_external_writing_context",
        "prepare_task_context",
        "read_imported_file",
        "search_task_context",
    }


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
    ("save_external_cataloging_facts", "skipped", {
        "validation_errors": ["facts[0].payload must be an object"],
        "validation_error_count": 1, "validation_errors_has_more": False,
        "allowed_fact_types": ["chapter_overview"],
        "next_tool": "save_external_cataloging_facts",
    }),
    ("save_external_cataloging_candidates", "ok", {
        "candidate_set_complete": False,
        "missing_required_items": ["character_state_update for declared characters (0/1)"],
        "candidates_saved": 2, "chapter_run_status": "facts_saved",
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


def test_cataloging_fact_schema_uses_the_persistence_enum_and_nested_payload():
    from app.modules.continuity.domain.cataloging_contract import CATALOGING_FACT_TYPES
    from app.services.workspace.tools.external_cataloging import CANONICAL_FACT_TYPES

    tool = registry.get("save_external_cataloging_facts")
    record = tool.input_schema["facts"]["items"]
    assert set(record["properties"]["fact_type"]["enum"]) == CANONICAL_FACT_TYPES
    assert set(CATALOGING_FACT_TYPES) == CANONICAL_FACT_TYPES
    assert record["properties"]["payload"]["type"] == "object"
    assert record["required"] == ["fact_type", "payload"]
