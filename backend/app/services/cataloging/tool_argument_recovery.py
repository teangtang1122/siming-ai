"""Bounded model correction with exact native calls and durable error receipts."""
from __future__ import annotations

import json

from ...architecture.uow import commit_session
from ...core.exceptions import LLMToolArgumentsError
from .constants import CATALOGING_STAGE_MAX_ATTEMPTS


def record_tool_argument_failure(db, run, messages, error: LLMToolArgumentsError,
                                failures: int, available_tools, step: int) -> dict:
    retryable = failures < CATALOGING_STAGE_MAX_ATTEMPTS
    calls = error.assistant_message["tool_calls"]
    messages.append(error.assistant_message)
    for call in calls:
        messages.append({
            "role": "tool", "tool_call_id": call["id"],
            "content": json.dumps({
                "tool": call["function"]["name"], "status": "error",
                "detail": "本批次 arguments JSON 无效，所有调用均未执行。请由模型按当前 Schema 重新提交完整 JSON 对象；不能把本批其他调用当作已成功。",
                "data": {"reason": "native_tool_arguments_invalid", "executed": False,
                         "retryable": retryable, "available_tools": sorted(available_tools),
                         "batch_call_count": len(calls), "validation_errors": error.validation_errors},
            }, ensure_ascii=False),
        })
    run.raw_output = json.dumps(messages[2:], ensure_ascii=False)
    commit_session(db)
    if not retryable:
        raise ValueError("建档模型累计三次返回无效工具参数 JSON；候选和原始响应已保留，整批未执行：" + error.message) from error
    return {
        "type": "cataloging_plan_correction", "status": "error",
        "reason": "native_tool_arguments_invalid", "attempt": failures, "step": step,
        "message": f"建档工具参数 JSON 无效，整批未执行；已保留原响应（{failures}/{CATALOGING_STAGE_MAX_ATTEMPTS}）：{error.message}",
    }
