"""Safe public projections for workspace-assistant failures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.modules.operations.interfaces.failures import classify_failure
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context.checkpoint_state import safe_public_error_detail

_CONTEXT_MESSAGES = {
    ConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY: (
        "当前作者消息超过所选模型的可验证容量，本次任务未执行。"
    ),
    ConversationContextErrorCode.CHECKPOINT_REQUIRED: ("对话历史需要先完成整理，本次任务未执行。"),
    ConversationContextErrorCode.PROTOCOL_INVALID: ("模型工具协议校验失败，本批次未执行。"),
    ConversationContextErrorCode.ORPHAN_TOOL_RESULT: ("模型工具结果缺少对应调用，本次任务未执行。"),
    ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION: (
        "模型工具事务不完整，本次任务未执行。"
    ),
    ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE: (
        "当前模型不具备可验证的 Agent 工具能力，请切换模型后重试。"
    ),
    ConversationContextErrorCode.PROVIDER_MAPPING_FAILED: (
        "模型消息协议映射失败，本次任务未执行。"
    ),
}
_REMEDIATION = {
    ConversationContextErrorCode.CAPACITY_UNKNOWN: "请配置模型上下文窗口或切换已验证模型。",
    ConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY: "请缩短当前消息或切换更大上下文模型。",
    ConversationContextErrorCode.CHECKPOINT_FAILED: (
        "请重试对话整理；本机 Agent CLI 需切换已验证的 API 模型，"
        "或新建对话；成功前不会执行工具。"
    ),
    ConversationContextErrorCode.CHECKPOINT_CANCELLED: "请重新发起任务以再次整理。",
    ConversationContextErrorCode.SOURCE_CHANGED: "请按当前完整记录重新发起任务。",
    ConversationContextErrorCode.PROTOCOL_INVALID: "请重试本轮或切换支持原生工具协议的模型。",
    ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE: (
        "请选择支持原生工具或 direct MCP 的模型。"
    ),
    ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY: "请切换更大上下文模型。",
    ConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY: (
        "请减少当前资料或切换更大上下文模型。"
    ),
}
_RETRYABLE_FALSE = {
    ConversationContextErrorCode.CURRENT_USER_OVER_CAPACITY,
    ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
    ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
}
_MODEL_FAILURE_RESPONSES: dict[str, tuple[str, str, bool]] = {
    "quota_or_rate_limit": (
        "model_quota_or_rate_limit",
        "模型额度已耗尽或请求受限，请稍后重试或切换模型。",
        True,
    ),
    "auth": (
        "model_authentication_failed",
        "模型授权已失效，请在模型设置中重新验证凭据。",
        False,
    ),
    "timeout": ("model_timeout", "模型响应超时，请稍后重试。", True),
    "network": ("model_network_error", "模型网络连接中断，请稍后重试。", True),
    "unavailable": ("model_unavailable", "当前模型暂不可用，请切换模型或稍后重试。", True),
    "empty_response": ("model_empty_response", "模型没有返回有效内容，请重试。", True),
    "invalid_response": ("model_invalid_response", "模型返回格式无法解析，请重试。", True),
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
_SAFE_REASON = re.compile(r"^[a-z0-9_]{1,100}$")
_NUMERIC_FIELDS = {
    "iteration",
    "call_index",
    "call_count",
    "actual_bytes",
    "max_bytes",
    "required_tokens",
    "available_tokens",
}


@dataclass(frozen=True)
class PublicAssistantFailure:
    code: str
    message: str
    details: dict[str, Any]
    failure_class: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    @property
    def persisted_error(self) -> str:
        return f"{self.code}: {self.message}"


def public_context_failure(error: ConversationContextError) -> PublicAssistantFailure:
    code = error.code
    message = _CONTEXT_MESSAGES.get(code) or safe_public_error_detail(code)
    if not message:
        message = "对话上下文处理失败，本次任务未执行。"
    details = _safe_context_details(code, error.details)
    details["retryable"] = code not in _RETRYABLE_FALSE
    remediation = _REMEDIATION.get(code)
    if remediation:
        details["remediation"] = remediation
    return PublicAssistantFailure(
        code=code.value,
        message=message,
        details=details,
        failure_class="conversation_context",
    )


def public_model_failure(error: Exception) -> PublicAssistantFailure:
    failure_class = classify_failure(str(error)) or "unknown"
    code, message, retryable = _MODEL_FAILURE_RESPONSES.get(
        failure_class,
        ("model_request_failed", "模型调用失败，请检查模型状态后重试。", True),
    )
    return PublicAssistantFailure(
        code=code,
        message=message,
        details={"failure_class": failure_class, "retryable": retryable},
        failure_class=failure_class,
    )


def public_model_error_message(code: str) -> str | None:
    """Return the allowlisted message for a persisted model error code."""

    for candidate_code, message, _retryable in _MODEL_FAILURE_RESPONSES.values():
        if candidate_code == code:
            return message
    if code == "model_request_failed":
        return "模型调用失败，请检查模型状态后重试。"
    return None


def public_server_failure(error_id: str) -> PublicAssistantFailure:
    return PublicAssistantFailure(
        code="workspace_assistant_server_error",
        message="工作台助手处理失败，请稍后重试。",
        details={"error_id": error_id, "retryable": True},
        failure_class="server_error",
    )


def safe_tool_execution_failure(error_id: str) -> dict[str, Any]:
    return {
        "status": "error",
        "detail": f"工具执行失败；错误编号：{error_id}",
        "data": {"reason": "tool_execution_failed", "error_id": error_id},
    }


def _safe_context_details(
    code: ConversationContextErrorCode,
    details: dict[str, Any],
) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for field in _NUMERIC_FIELDS:
        value = details.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[field] = value
    if code in {
        ConversationContextErrorCode.CAPACITY_UNKNOWN,
        ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
    }:
        for field in ("provider", "model", "capacity_assurance"):
            value = str(details.get(field) or "").strip()
            if _SAFE_TOKEN.fullmatch(value):
                safe[field] = value
    if code is ConversationContextErrorCode.PROTOCOL_INVALID:
        tool = str(details.get("tool") or "").strip()
        if _SAFE_TOKEN.fullmatch(tool):
            safe["tool"] = tool
        tools = details.get("tools")
        if isinstance(tools, list):
            safe_tools = [str(item) for item in tools[:12] if _SAFE_TOKEN.fullmatch(str(item))]
            if safe_tools:
                safe["tools"] = safe_tools
        reason = str(details.get("reason") or "")
        if _SAFE_REASON.fullmatch(reason):
            safe["reason"] = reason
    return safe


__all__ = [
    "PublicAssistantFailure",
    "public_context_failure",
    "public_model_failure",
    "public_server_failure",
    "safe_tool_execution_failure",
]
