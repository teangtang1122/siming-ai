import pytest

from app.routers.ai_writer import _workspace_outcome
from app.services.workspace.assistant_response import (
    _append_workspace_failure_notice,
    _resolve_workspace_failures,
)


def test_workspace_outcome_marks_empty_response():
    outcome = _workspace_outcome(
        "",
        applied_actions=[],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "empty_response"


def test_workspace_outcome_marks_tool_completion_without_text_reply():
    outcome = _workspace_outcome(
        "",
        applied_actions=[{"tool": "chapter_writer", "status": "ok"}],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "completed_with_tools"


def test_workspace_outcome_marks_failures():
    outcome = _workspace_outcome(
        "已处理",
        applied_actions=[],
        tool_logs=[{"tool": "json_repair", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "json_repair", "status": "error"}],
    )

    assert outcome == "failed"


def test_workspace_outcome_marks_partial_success_when_a_write_succeeded_before_failure():
    outcome = _workspace_outcome(
        "草稿已生成，但编辑器通知失败",
        applied_actions=[{"tool": "chapter_writer", "status": "ok"}],
        tool_logs=[{"tool": "notify_editor", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "notify_editor", "status": "error"}],
    )

    assert outcome == "partial_success"


@pytest.mark.parametrize("tool,label", [
    ("chapter_writer", "章节草稿"),
    ("save_external_chapter_draft", "章节草稿"),
    ("outline_writer", "大纲草稿"),
    ("save_external_outline_draft", "大纲草稿"),
])
@pytest.mark.parametrize("status", ["error", "failed", "interrupted", "needs_confirmation"])
def test_terminal_draft_marks_failed_context_submission_as_recovered(tool, label, status):
    failed_submit = {"tool": "submit_context_evidence", "status": status}
    applied_actions = [
        {
            "tool": tool,
            "status": "ok",
            "data": {
                "draft_id": "draft-12",
                "draft_status": "pending",
                "context_manifest_id": "manifest-12",
            },
        }
    ]

    resolution = _resolve_workspace_failures([failed_submit], applied_actions)
    outcome = _workspace_outcome(
        "章节草稿已生成并载入正文编辑器，尚未保存。",
        applied_actions=applied_actions,
        tool_logs=[failed_submit, *applied_actions],
        searched_context=[],
        failed_logs=resolution.unresolved,
    )
    reply = _append_workspace_failure_notice(
        "章节草稿已生成并载入正文编辑器，尚未保存。",
        resolution,
    )

    assert resolution.unresolved == []
    assert resolution.recovered == [failed_submit]
    assert outcome == "completed_with_reply"
    assert "后续流程已纠正" in reply
    assert f"{label}已成功生成并暂存" in reply
    assert "相关数据可能未保存" not in reply
    assert "执行失败" not in reply
    assert "等待确认" not in reply


@pytest.mark.parametrize("status", ["error", "failed", "interrupted"])
@pytest.mark.parametrize("tool", ["chapter_writer", "save_external_chapter_draft"])
def test_terminal_draft_does_not_hide_unrelated_failed_operation(tool, status):
    failed_notification = {"tool": "notify_editor", "status": status}
    applied_actions = [
        {
            "tool": tool,
            "status": "ok",
            "data": {
                "draft_id": "draft-12",
                "draft_status": "pending",
                "context_manifest_id": "manifest-12",
            },
        }
    ]

    resolution = _resolve_workspace_failures([failed_notification], applied_actions)
    reply = _append_workspace_failure_notice("草稿已生成。", resolution)

    assert resolution.unresolved == [failed_notification]
    assert resolution.recovered == []
    assert "章节草稿已成功生成并暂存" in reply
    assert "相关附加操作可能未完成" in reply
    assert "相关数据可能未保存" not in reply
    assert reply.count("notify_editor") == 1


def test_terminal_draft_resolves_retried_read_capacity_denials():
    denied = [
        {"tool": tool, "status": "error", "remediation": {
            "code": "tool_result_batch_over_capacity", "retryable": True,
        }}
        for tool in ("list_chapters", "search_outline_tree")
    ]
    retried = [{"tool": log["tool"], "status": "ok"} for log in denied]
    draft = {"tool": "chapter_writer", "status": "ok", "data": {
        "draft_id": "draft-12", "draft_status": "pending", "context_manifest_id": "manifest-12",
    }}
    logs = [*denied, *retried, draft]

    resolution = _resolve_workspace_failures(logs, [draft])

    assert resolution.recovered == denied
    assert resolution.unresolved == []
    assert _workspace_outcome("", applied_actions=[draft], tool_logs=logs,
                              searched_context=[], failed_logs=resolution.unresolved) == "completed_with_tools"


def test_read_denial_remains_unresolved_without_successful_retry():
    denied = {"tool": "list_chapters", "status": "error", "remediation": {
        "code": "tool_result_batch_over_capacity", "retryable": True,
    }}
    draft = {"tool": "chapter_writer", "status": "ok", "data": {
        "draft_id": "draft-12", "draft_status": "pending", "context_manifest_id": "manifest-12",
    }}

    resolution = _resolve_workspace_failures([denied, draft], [draft])

    assert resolution.unresolved == [denied]
    assert resolution.recovered == []


@pytest.mark.parametrize("action", [
    {"tool": "chapter_writer", "status": "ok"},
    {"tool": "chapter_writer", "status": "ok", "data": {"draft_status": "pending"}},
    {"tool": "chapter_writer", "status": "ok", "data": {"draft_id": "draft-1"}},
    *[
        {"tool": "chapter_writer", "status": status,
         "data": {"draft_id": "draft-1", "draft_status": "pending", "context_manifest_id": "manifest-1"}}
        for status in ("ready", "blocked", "error", "needs_confirmation")
    ],
    {"tool": "chapter_writer", "status": "ok",
     "data": {"draft_id": "draft-1", "draft_status": "discarded", "context_manifest_id": "manifest-1"}},
    {"tool": "unknown_writer", "status": "ok",
     "data": {"draft_id": "draft-1", "draft_status": "pending", "context_manifest_id": "manifest-1"}},
])
def test_draft_success_is_never_inferred_without_a_registered_durable_receipt(action):
    failed = {"tool": "submit_context_evidence", "status": "error"}
    resolution = _resolve_workspace_failures([failed], [action])
    assert resolution.unresolved == [failed]
    assert resolution.recovered == []
    assert resolution.terminal_draft_tool is None


def test_draft_without_context_receipt_does_not_prove_context_recovered():
    failed = {"tool": "submit_context_evidence", "status": "error"}
    action = {"tool": "chapter_writer", "status": "ok",
              "data": {"draft_id": "draft-1", "draft_status": "pending"}}
    resolution = _resolve_workspace_failures([failed], [action])
    assert resolution.unresolved == [failed]
    assert resolution.recovered == []
    assert resolution.terminal_draft_tool == "chapter_writer"


@pytest.mark.parametrize("with_draft", [False, True])
def test_unresolved_confirmation_is_visible_but_not_reported_as_failure(with_draft):
    pending = {"tool": "confirm_outline", "status": "needs_confirmation"}
    actions = [{"tool": "chapter_writer", "status": "ok",
                "data": {"draft_id": "draft-1", "draft_status": "pending", "context_manifest_id": "manifest-1"}}] if with_draft else []
    resolution = _resolve_workspace_failures([pending], actions)
    reply = _append_workspace_failure_notice("本轮结束。", resolution)
    assert resolution.unresolved == [pending]
    assert "需要确认或调整" in reply
    assert "执行失败" not in reply
    assert "可能未保存" not in reply
    assert reply.count("confirm_outline") == 1
    assert _workspace_outcome(reply, applied_actions=actions, tool_logs=[pending],
                              searched_context=[], failed_logs=resolution.unresolved) == "waiting_user"


def test_error_and_confirmation_are_reported_separately():
    logs = [{"tool": "notify_editor", "status": "error"},
            {"tool": "submit_context_evidence", "status": "needs_confirmation"}]
    resolution = _resolve_workspace_failures(logs, [])
    reply = _append_workspace_failure_notice("本轮结束。", resolution)
    assert "notify_editor 执行失败" in reply
    assert "未完成：submit_context_evidence 等待确认" in reply
    assert "submit_context_evidence 执行失败" not in reply
    assert _workspace_outcome(reply, applied_actions=[], tool_logs=logs,
                              searched_context=[], failed_logs=resolution.unresolved) == "failed"
