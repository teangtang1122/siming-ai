"""Failure metadata for the resumable new-book creation flow."""
from __future__ import annotations

from typing import Any


def clear_stage_failure(last_error: Any, stage: str) -> Any:
    if isinstance(last_error, dict) and str(last_error.get("failed_stage") or "").strip() == stage:
        return None
    return last_error


def build_stage_failure(
    *,
    failure_class: str,
    message: str,
    run_id: str,
    failed_stage: str,
    failed_stage_label: str,
) -> tuple[str, dict[str, Any]]:
    advice = {
        "quota_or_rate_limit": "切换可用模型或等待额度恢复后重试本阶段",
        "auth": "在系统设置中重新填写凭据并测试连接",
        "timeout": "重试本阶段，或切换响应更快的模型",
        "empty_response": "重试本阶段；若持续发生，请测试模型的结构化输出",
        "invalid_response": f"草稿已保留，请重试“{failed_stage_label}”；若仍失败可切换模型",
        "tool_unavailable": "改用已启用司命工具的 CLI，或切换 API 模型",
    }.get(failure_class, "保留当前草稿，检查模型后重试本阶段")
    return advice, {
        "failure_class": failure_class,
        "message": message,
        "next_action": advice,
        "run_id": run_id,
        "failed_stage": failed_stage,
        "failed_stage_label": failed_stage_label,
    }
