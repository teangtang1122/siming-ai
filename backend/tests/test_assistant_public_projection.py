from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.workspace.assistant_public_projection import (
    public_message_payload,
    public_run_mapping,
    public_step_payload,
    public_tool_log,
)


def test_persisted_rate_limit_run_uses_the_specific_safe_message() -> None:
    projected = public_run_mapping({
        "status": "error",
        "current_iteration": 1,
        "error": "model_quota_or_rate_limit: private provider detail",
    })

    assert projected["error_code"] == "model_quota_or_rate_limit"
    assert projected["error"] == "模型额度已耗尽或请求受限，请稍后重试或切换模型。"
    assert "private provider detail" not in json.dumps(projected, ensure_ascii=False)


def _resource_uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def test_success_sse_projection_never_includes_raw_result_data() -> None:
    secret = "provider-secret-success-result"
    projected = public_tool_log(
        {
            "tool": "read_project_file",
            "status": "ok",
            "detail": f"read /srv/private with {secret}",
            "data": {
                "path": "/srv/private/project.md",
                "folder_path": "/srv/private",
                "directory": "/srv",
                "content": secret,
                "nested": {"accessToken": secret, "apiKey": secret},
            },
        }
    )

    assert projected == {
        "tool": "read_project_file",
        "status": "ok",
        "detail": "read_project_file 已完成",
    }
    assert secret not in json.dumps(projected, ensure_ascii=False)


def test_denied_sse_projection_never_includes_diagnostic_or_nested_data() -> None:
    secret = "denied-result-private-data"
    projected = public_tool_log(
        {
            "tool": "search_chapters",
            "status": "error",
            "detail": f"provider rejected /srv/private {secret}",
            "data": {
                "reason": "tool_result_batch_over_capacity",
                "path": "/srv/private",
                "nested": {"accessToken": secret, "content": secret},
            },
        }
    )

    assert projected == {
        "tool": "search_chapters",
        "status": "error",
        "detail": "search_chapters 执行失败",
    }
    assert secret not in json.dumps(projected, ensure_ascii=False)


def test_short_draft_projection_exposes_only_safe_retry_counts() -> None:
    secret = "private-draft-diagnostic"
    raw = {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": f"provider path /srv/private {secret}",
        "data": {
            "reason_code": "draft_below_minimum",
            "actual_han_characters": 3_612,
            "minimum_han_characters": 3_800,
            "missing_han_characters": 999_999,
            "context_manifest_id": secret,
            "context_selection_token": secret,
            "nested": {"apiKey": secret},
        },
    }

    projected = public_tool_log(raw)

    assert projected == {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": "正文有 3612 个汉字，低于最低要求 3800 个；至少还差 188 个。为减少反复退回，建议一次补至 4180 个汉字（约再补 568 个）后重试。",
        "remediation": {
            "code": "draft_below_minimum",
            "message": "正文有 3612 个汉字，低于最低要求 3800 个；至少还差 188 个。为减少反复退回，建议一次补至 4180 个汉字（约再补 568 个）后重试。",
            "retryable": True,
            "actual_han_characters": 3_612,
            "minimum_han_characters": 3_800,
            "missing_han_characters": 188,
            "recommended_han_characters": 4_180,
            "recommended_additional_han_characters": 568,
        },
    }
    assert secret not in json.dumps(projected, ensure_ascii=False)

    step = SimpleNamespace(
        id="step-short-draft",
        run_id="run-1",
        step_type="write",
        tool="save_external_chapter_draft",
        status="needs_confirmation",
        iteration=1,
        attempt_no=1,
        retry_of_step_id=None,
        resolved_step_id=None,
        request_json=json.dumps({"content": secret}),
        result_json=json.dumps(raw, ensure_ascii=False),
        output_refs=None,
        started_at=None,
        completed_at=None,
    )
    projected_step = public_step_payload(step, can_retry=True, retry_block_reason=None)
    assert projected_step["detail"] == projected["detail"]
    assert projected_step["remediation"] == projected["remediation"]
    assert secret not in json.dumps(projected_step, ensure_ascii=False)


def test_unknown_retry_reason_keeps_generic_projection() -> None:
    secret = "unregistered-retry-secret"
    projected = public_tool_log({
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": secret,
        "data": {"reason_code": "provider_private_reason", "apiKey": secret},
    })

    assert projected == {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": "save_external_chapter_draft 状态已更新",
    }
    assert secret not in json.dumps(projected, ensure_ascii=False)


@pytest.mark.parametrize("tool_name", ["chapter_writer", "save_external_chapter_draft"])
def test_public_chapter_draft_receipt_keeps_full_editor_content_only(tool_name: str) -> None:
    secret = "nested-chapter-receipt-secret"
    content = "正文" * 2_000
    payload = public_message_payload(
        {
            "applied_actions": [
                {
                    "tool": tool_name,
                    "status": "ok",
                    "detail": f"raw detail {secret}",
                    "arguments": {"apiKey": secret},
                    "data": {
                        "draft_id": "draft-1",
                        "project_id": "project-1",
                        "content_ref": "draft-1",
                        "title": "第一章",
                        "outline_node_id": "outline-1",
                        "context_manifest_id": "manifest-1",
                        "saved_chapter_id": None,
                        "draft_status": "pending",
                        "word_count": 4_000,
                        "next_actions": ["save_only", "save_and_catalog"],
                        "content": content,
                        "path": f"/srv/{secret}",
                        "folder_path": secret,
                        "directory": secret,
                        "provider": {"accessToken": secret, "apiKey": secret},
                        "hidden": {"reasoning": secret, "content": secret},
                    },
                }
            ]
        }
    )

    assert payload is not None
    action = payload["applied_actions"][0]
    assert action["detail"] == f"{tool_name} 已完成"
    assert action["data"] == {
        "draft_id": "draft-1",
        "project_id": "project-1",
        "content_ref": "draft-1",
        "title": "第一章",
        "outline_node_id": "outline-1",
        "saved_chapter_id": None,
        "draft_status": "pending",
        "next_actions": ["save_only", "save_and_catalog"],
        "word_count": 4_000,
        "context_manifest_id": "manifest-1",
        "content": content,
    }
    assert secret not in json.dumps(payload, ensure_ascii=False)
    persisted = public_message_payload(payload)
    assert persisted is not None
    assert persisted["applied_actions"][0]["data"]["content"] == content


@pytest.mark.parametrize("tool_name", ["outline_writer", "save_external_outline_draft"])
def test_public_outline_draft_receipt_allowlists_each_full_editor_node(tool_name: str) -> None:
    secret = "nested-outline-receipt-secret"
    summary = "长摘要" * 1_000
    payload = public_message_payload(
        {
            "applied_actions": [
                {
                    "tool": tool_name,
                    "status": "ok",
                    "detail": secret,
                    "data": {
                        "draft_id": "outline-draft-1",
                        "project_id": "project-1",
                        "context_manifest_id": "manifest-1",
                        "parent_id": None,
                        "insert_after_id": "outline-1",
                        "draft_status": "pending",
                        "design_notes": "作者可见设计说明",
                        "saved_outline_node_ids": [],
                        "chapter_outline_node_ids": ["proposal-node-1"],
                        "next_actions": ["edit", "confirm"],
                        "nodes": [
                            {
                                "id": "proposal-node-1",
                                "parent_id": None,
                                "node_type": "chapter",
                                "title": "第二章",
                                "summary": summary,
                                "parent_title": None,
                                "actual_summary": None,
                                "planned_summary": summary,
                                "character_names": ["甲", "乙"],
                                "status": "pending",
                                "path": secret,
                                "folder_path": secret,
                                "directory": secret,
                                "accessToken": secret,
                                "apiKey": secret,
                                "content": secret,
                                "metadata": {"apiKey": secret, "path": secret},
                            }
                        ],
                        "internal": {"reasoning": secret, "content": secret},
                    },
                }
            ]
        }
    )

    assert payload is not None
    data = payload["applied_actions"][0]["data"]
    assert data["draft_id"] == "outline-draft-1"
    assert data["nodes"] == [
        {
            "id": "proposal-node-1",
            "parent_id": None,
            "node_type": "chapter",
            "title": "第二章",
            "summary": summary,
            "parent_title": None,
            "actual_summary": None,
            "planned_summary": summary,
            "character_names": ["甲", "乙"],
            "status": "pending",
        }
    ]
    assert secret not in json.dumps(payload, ensure_ascii=False)
    persisted = public_message_payload(payload)
    assert persisted is not None
    assert persisted["applied_actions"][0]["data"]["nodes"] == data["nodes"]


def test_non_draft_action_has_no_data_and_resource_receipt_uses_output_refs() -> None:
    secret = "resource-receipt-secret"
    payload = public_message_payload(
        {
            "applied_actions": [
                {
                    "tool": "create_character",
                    "status": "ok",
                    "detail": secret,
                    "data": {
                        "character_id": "character-1",
                        "revision": 7,
                        "nested": {"path": secret, "accessToken": secret},
                    },
                }
            ]
        }
    )
    assert payload is not None
    assert payload["applied_actions"] == [
        {
            "tool": "create_character",
            "status": "ok",
            "detail": "create_character 已完成",
        }
    ]

    step = SimpleNamespace(
        id="step-1",
        run_id="run-1",
        step_type="write",
        tool="create_character",
        status="ok",
        iteration=1,
        attempt_no=1,
        retry_of_step_id=None,
        resolved_step_id=None,
        request_json=json.dumps({"apiKey": secret}),
        result_json=json.dumps({"content": secret}),
        output_refs=json.dumps(
            {"character": {"id": _resource_uuid(1), "revision": 7}},
            ensure_ascii=False,
        ),
        started_at=None,
        completed_at=None,
    )
    projected_step = public_step_payload(step, can_retry=False, retry_block_reason=None)

    assert projected_step["resource_refs"] == [
        {"type": "character", "id": _resource_uuid(1), "revision": 7}
    ]
    assert secret not in json.dumps(projected_step, ensure_ascii=False)


def test_resource_receipt_revision_projection_rejects_legacy_payload_text() -> None:
    secret = "SECRET manuscript /tmp/private/key " + ("x" * 5_000)
    short_token = "sk-private-token"
    step = SimpleNamespace(
        id="step-legacy-refs",
        run_id="run-1",
        step_type="write",
        tool="update_character",
        status="ok",
        iteration=1,
        attempt_no=1,
        retry_of_step_id=None,
        resolved_step_id=None,
        request_json=None,
        result_json=None,
        output_refs=json.dumps(
            {
                "character": [
                    {"id": _resource_uuid(1), "revision": 0},
                    {"id": _resource_uuid(2), "revision": short_token},
                    {"id": _resource_uuid(3), "revision": secret},
                    {"id": _resource_uuid(4), "revision": "/tmp/private/key"},
                    {"id": _resource_uuid(5), "revision": -1},
                    {"id": _resource_uuid(6), "revision": True},
                    {"id": _resource_uuid(7), "revision": {"apiKey": secret}},
                    {"id": _resource_uuid(8), "revision": [secret]},
                    {"id": "/tmp/private/key", "revision": 9},
                    {"id": short_token, "revision": 10},
                    # Gateway sync may preserve this legitimate legacy shape,
                    # but the broad grammar is intentionally not public-safe.
                    {"id": "mobile:character-legacy-1", "revision": 11},
                ],
                # Even a syntactically valid UUID is not public when its type
                # is outside the producer-backed static contract.
                "sk-private-token": {"id": _resource_uuid(9), "revision": 12},
            },
            ensure_ascii=False,
        ),
        started_at=None,
        completed_at=None,
    )

    projected = public_step_payload(step, can_retry=False, retry_block_reason=None)

    assert projected["resource_refs"] == [
        {"type": "character", "id": _resource_uuid(1), "revision": 0},
        {"type": "character", "id": _resource_uuid(2)},
        {"type": "character", "id": _resource_uuid(3)},
        {"type": "character", "id": _resource_uuid(4)},
        {"type": "character", "id": _resource_uuid(5)},
        {"type": "character", "id": _resource_uuid(6)},
        {"type": "character", "id": _resource_uuid(7)},
        {"type": "character", "id": _resource_uuid(8)},
    ]
    assert secret not in json.dumps(projected, ensure_ascii=False)
    assert short_token not in json.dumps(projected, ensure_ascii=False)
