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


def test_terminal_draft_marks_failed_context_submission_as_recovered():
    failed_submit = {"tool": "submit_context_evidence", "status": "error"}
    applied_actions = [
        {
            "tool": "save_external_chapter_draft",
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
    assert "章节草稿已成功生成并暂存" in reply
    assert "相关数据可能未保存" not in reply


def test_terminal_draft_does_not_hide_unrelated_failed_operation():
    failed_notification = {"tool": "notify_editor", "status": "error"}
    applied_actions = [
        {
            "tool": "save_external_chapter_draft",
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


def test_recovered_short_draft_checks_are_presented_as_length_expansion():
    short_draft_checks = [
        {
            "tool": "save_external_chapter_draft",
            "status": "needs_confirmation",
            "remediation": {
                "code": "draft_below_minimum",
                "actual_han_characters": count,
                "minimum_han_characters": 3_400,
            },
        }
        for count in (2_215, 2_631, 3_068, 3_322)
    ]
    applied_actions = [
        {
            "tool": "save_external_chapter_draft",
            "status": "ok",
            "data": {
                "draft_id": "draft-39",
                "draft_status": "pending",
                "context_manifest_id": "manifest-39",
            },
        }
    ]

    resolution = _resolve_workspace_failures(short_draft_checks, applied_actions)
    reply = _append_workspace_failure_notice("章节草稿已生成。", resolution)

    assert resolution.unresolved == []
    assert resolution.recovered == short_draft_checks
    assert "经过 4 次篇幅校验与补写" in reply
    assert "最终章节草稿已达到要求并成功暂存" in reply
    assert "前序工具调用未通过" not in reply
