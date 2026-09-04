"""Fail-closed request contract for the durable workspace conversation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ai_writer import WorkspaceAssistantRequest


@pytest.mark.parametrize("legacy_field", ["history", "assistant_history", "messages"])
def test_workspace_assistant_request_rejects_legacy_history_fields(
    legacy_field: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        WorkspaceAssistantRequest.model_validate(
            {
                "message": "继续当前任务",
                legacy_field: [{"role": "user", "content": "旧客户端历史"}],
            }
        )

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"
    assert exc_info.value.errors()[0]["loc"] == (legacy_field,)
