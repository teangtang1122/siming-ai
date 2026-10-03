"""Public error projection for the creation conversation."""
from __future__ import annotations

from typing import Any

from app.services.conversation_context import ConversationContextError, ConversationContextErrorCode
from app.services.conversation_context.checkpoint_state import safe_public_error_detail
from app.services.observability.run_events import classify_failure


def safe_creation_agent_error(exc: Exception) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, ConversationContextError):
        if exc.code is ConversationContextErrorCode.TOOL_TRANSACTION_OVER_CAPACITY:
            from .workspace.assistant_public_errors import public_context_failure

            failure = public_context_failure(exc)
            return failure.message, {
                "error_type": "conversation_context",
                "failure_class": failure.failure_class,
                **failure.to_dict(),
                "next_action": failure.details["remediation"],
            }
        code = exc.code.value
        message = safe_public_error_detail(exc.code) or (
            "对话上下文处理失败，本次任务未执行。"
        )
        next_action = (
            "请缩小当前请求、检查模型容量或重试上下文整理；"
            "在上下文通过校验前不会执行任何业务工具。"
        )
        return message, {
            "error_type": "conversation_context",
            "failure_class": "conversation_context",
            "code": code,
            "message": message,
            "details": {"remediation": next_action},
            "next_action": next_action,
        }
    failure_class = classify_failure(str(exc)) or "unknown"
    message, next_action = {
        "quota_or_rate_limit": (
            "模型额度已耗尽或请求受限",
            "请等待额度恢复，或切换到有额度的模型后重试。",
        ),
        "auth": ("模型授权已失效", "请到模型设置重新登录或填写凭据，测试成功后重试。"),
        "timeout": ("模型响应超时", "后台状态已保留，可稍后重试或切换更快的模型。"),
        "network": ("模型网络连接中断", "请检查网络或本机模型进程后重试。"),
        "empty_response": ("模型没有返回有效内容", "请重试本轮或切换模型。"),
        "invalid_response": ("模型返回格式无法解析", "请重试本轮或切换模型。"),
        "provider_protocol": (
            "模型接口拒绝了工具或思考协议",
            "请检查模型设置中的接口协议，或切换模型后重试；仍失败时请提供错误编号和诊断日志。",
        ),
    }.get(failure_class, ("立项助手处理失败", "请检查模型状态后重试本轮。"))
    return message, {
        "error_type": type(exc).__name__,
        "failure_class": failure_class,
        "next_action": next_action,
    }
