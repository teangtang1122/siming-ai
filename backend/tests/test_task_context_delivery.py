"""Context pages must reconstruct exact source text and fit the actual receipt."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services.task_context_delivery import (
    begin_context_delivery,
    build_context_page,
    context_delivery_ready,
    context_delivery_state,
    context_selection_diagnostics,
    deliver_next_context_page,
)
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import model_tool_result_projector


@pytest.mark.parametrize("text", ["汉字与档案。" * 4000, "𠀀😀\x00\n\"" * 4000, ""], ids=["han", "unicode-and-controls", "empty"])
def test_pages_reconstruct_the_entire_document_without_splitting_code_points(text):
    args = {"content_limit": 7000}
    parts = []
    while True:
        page = build_context_page(text, args)
        assert page["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert len(json.dumps(page["text"], ensure_ascii=False).encode("utf-8")) <= 20 * 1024
        parts.append(page["text"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        assert page["next_cursor"] > page["cursor"]
        args = {"content_cursor": page["next_cursor"], "content_limit": 7000,
                "expected_context_sha256": page["sha256"]}
    assert "".join(parts) == text


def test_failed_source_diagnostics_fit_native_receipt_without_source_bodies():
    rejected = [{"item_id": "𠀀" * 500, "source_id": "😀" * 500,
                 "reason": "字段已变更𠀀" * 500, "source": "NEVER_INCLUDE_SOURCE_BODY" * 500} for _ in range(20)]
    diagnostics = context_selection_diagnostics(rejected)
    projected = model_tool_result_projector.project(registry.get_spec("submit_context_evidence"), {
        "tool": "submit_context_evidence", "status": "needs_confirmation", "detail": "Refresh invalid sources",
        "data": {"manifest_id": "manifest-1", "accepted_count": 0, "selection_ready": False,
                 "rejected": rejected, **diagnostics},
    })
    data = projected.payload["data"]
    assert data["validation_error_count"] == 20
    assert data["validation_errors_has_more"] is True
    assert len(data["validation_errors"]) == 6
    assert "NEVER_INCLUDE_SOURCE_BODY" not in projected.content
    assert len(projected.content.encode("utf8")) < 16 * 1024


@pytest.mark.parametrize("args", [
    {"content_cursor": -1}, {"content_cursor": 10}, {"content_cursor": "1"},
    {"content_limit": 0}, {"content_limit": 7001}, {"content_limit": True},
    {"expected_context_sha256": "old-hash"},
])
def test_invalid_or_stale_page_requests_fail_explicitly(args):
    with pytest.raises(ValueError):
        build_context_page("正文", args)


def test_selected_evidence_page_survives_the_actual_model_receipt_projection():
    text = "档案原文𠀀\n" * 4000 + "最后一条证据不可丢失"
    args = {}
    parts = []
    while True:
        page = build_context_page(text, args)
        next_arguments = {
            "context_manifest_id": "manifest-1",
            "task_type": "writing",
            "content_cursor": page["next_cursor"],
            "content_limit": page["limit"],
            "expected_context_sha256": page["sha256"],
        } if page["has_more"] else None
        projected = model_tool_result_projector.project(
            registry.get_spec("submit_context_evidence"),
            {
                "tool": "submit_context_evidence", "status": "ok", "detail": "Evidence selected",
                "data": {
                    "context_manifest_id": "manifest-1", "manifest_id": "manifest-1",
                    "context_selection_token": "selection-1", "selection_ready": True,
                    "accepted_count": 2, "context_page": page,
                    "next_tool": "prepare_task_context" if page["has_more"] else None,
                    "next_arguments": next_arguments,
                },
            },
        )
        received = projected.payload["data"]
        assert received["context_page"] == page
        assert received["next_arguments"] == next_arguments
        assert len(projected.content.encode("utf-8")) <= 24 * 1024
        parts.append(received["context_page"]["text"])
        if not page["has_more"]:
            break
        args = next_arguments
    assert "".join(parts) == text


def test_default_page_budget_avoids_serial_turn_explosion_for_chinese_context():
    text = "林澄核对处置附件与来源限制。" * 3000
    args = {}
    page_count = 0
    while True:
        page = build_context_page(text, args)
        page_count += 1
        if not page["has_more"]:
            break
        args = {"content_cursor": page["next_cursor"], "content_limit": page["limit"],
                "expected_context_sha256": page["sha256"]}
    assert page_count <= 8


def test_selected_context_token_is_gated_until_every_page_is_delivered_in_order():
    text = "第一个证据段。" * 2400 + "最终证据"
    token = "secret-selection-token"
    manifest = SimpleNamespace(query_json={})
    first = build_context_page(text, {"content_limit": 1000})
    state = begin_context_delivery(manifest, first, token)

    assert state["status"] == "pending"
    assert context_delivery_ready(manifest, token) is False
    assert "secret-selection-token" not in json.dumps(manifest.query_json)

    exact = {
        "content_cursor": first["next_cursor"],
        "content_limit": first["limit"],
        "expected_context_sha256": first["sha256"],
    }
    for invalid in (
        {**exact, "content_cursor": exact["content_cursor"] + 1},
        {**exact, "content_limit": exact["content_limit"] + 1},
        {**exact, "expected_context_sha256": "wrong"},
    ):
        with pytest.raises(ValueError):
            deliver_next_context_page(manifest, text, invalid, token)
    with pytest.raises(ValueError):
        deliver_next_context_page(manifest, text, exact, "different-token")

    parts = [first["text"]]
    last_args = exact
    while True:
        page, state = deliver_next_context_page(manifest, text, last_args, token)
        parts.append(page["text"])
        if not page["has_more"]:
            break
        assert context_delivery_ready(manifest, token) is False
        last_args = {
            "content_cursor": page["next_cursor"],
            "content_limit": page["limit"],
            "expected_context_sha256": page["sha256"],
        }

    assert "".join(parts) == text
    assert state["delivered_until"] == len(text)
    assert context_delivery_ready(manifest, token) is True
    assert context_delivery_state(manifest)["expected_cursor"] is None

    replay, replay_state = deliver_next_context_page(manifest, text, last_args, token)
    assert replay == page
    assert replay_state == state
    with pytest.raises(ValueError):
        deliver_next_context_page(
            manifest,
            text,
            {**last_args, "content_cursor": last_args["content_cursor"] - 1},
            token,
        )
