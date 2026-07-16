from app.routers.ai_writer import _assistant_history_text, _workspace_outcome


def test_workspace_outcome_marks_empty_response():
    outcome = _workspace_outcome(
        "",
        all_actions=[],
        applied_actions=[],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "empty_response"


def test_workspace_outcome_marks_tool_completion_without_text_reply():
    outcome = _workspace_outcome(
        "",
        all_actions=[],
        applied_actions=[{"tool": "create_chapter", "status": "ok"}],
        tool_logs=[],
        searched_context=[],
    )

    assert outcome == "completed_with_tools"


def test_workspace_outcome_marks_failures():
    outcome = _workspace_outcome(
        "已处理",
        all_actions=[],
        applied_actions=[],
        tool_logs=[{"tool": "json_repair", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "json_repair", "status": "error"}],
    )

    assert outcome == "failed"


def test_workspace_outcome_marks_partial_success_when_a_write_succeeded_before_failure():
    outcome = _workspace_outcome(
        "章节已创建，但档案更新失败",
        all_actions=[],
        applied_actions=[{"tool": "create_chapter", "status": "ok"}],
        tool_logs=[{"tool": "archive_chapter_after_write", "status": "error"}],
        searched_context=[],
        failed_logs=[{"tool": "archive_chapter_after_write", "status": "error"}],
    )

    assert outcome == "partial_success"


def test_workspace_outcome_marks_confirmation_as_waiting_user():
    outcome = _workspace_outcome(
        "请确认后继续",
        all_actions=[],
        applied_actions=[],
        tool_logs=[],
        searched_context=[],
        needs_confirmation=True,
    )

    assert outcome == "waiting_user"


def test_workspace_history_labels_assistant_without_completion_state():
    history = _assistant_history_text([
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "我在。"},
    ])

    assert "助手：" in history
    assert "助手（已完成）" not in history
