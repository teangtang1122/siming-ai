"""Small deterministic helpers for workspace assistant turns."""

from __future__ import annotations

import json
from typing import Any

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
    tool_names_for_categories,
)
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ReferenceContext,
)


def reference_context_record(reference: ReferenceContext | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    return reference.model_dump(mode="json")


def reference_context_audit(reference: ReferenceContext | None) -> dict[str, Any] | None:
    record = reference_context_record(reference)
    if record is None:
        return None
    return {
        "source_kind": record["source_kind"],
        "source_name": record["source_name"],
        "coverage": record["coverage"],
        "source_chars": record["source_chars"],
        "content_sha256": record["content_sha256"],
    }


def load_durable_reference_context(
    message: Any,
    *,
    expected: dict[str, Any] | None,
) -> ReferenceContext | None:
    """Reload the current turn's data-only reference from its durable row."""

    try:
        payload = json.loads(message.payload_json) if message.payload_json else {}
        stored = payload.get("reference_context") if isinstance(payload, dict) else None
        reference = ReferenceContext.model_validate(stored) if stored is not None else None
    except Exception as exc:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "当前任务的引用资料审计记录无效；本轮未执行任何业务工具。",
            details={
                "message_id": str(getattr(message, "id", "") or ""),
                "remediation": "请重新附加资料后重试。",
            },
        ) from exc
    actual = reference_context_record(reference)
    if actual != expected:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "当前任务的引用资料在执行期间发生变化；本轮未执行任何业务工具。",
            details={
                "message_id": str(getattr(message, "id", "") or ""),
                "expected_content_sha256": expected.get("content_sha256") if expected else None,
                "actual_content_sha256": actual.get("content_sha256") if actual else None,
                "remediation": "请重新附加资料后重试。",
            },
        )
    return reference


def workspace_category_result(
    arguments: dict[str, Any],
    authorized_tool_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    try:
        categories = normalize_tool_categories(arguments.get("enabled_categories"))
    except ValueError:
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "error",
            "detail": "工具类别参数无效，未切换能力。",
            "data": None,
        }, None
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    available = authorized_tool_names & set(tool_names_for_categories(categories))
    detail = (
        f"已准备{'、'.join(labels)}能力，共 {len(available)} 项可用工具"
        if labels
        else "已关闭全部业务工具"
    )
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": {
            "enabled_categories": list(categories),
            "labels": labels,
            "available_tool_count": len(available),
        },
    }, categories


def workspace_category_instruction(
    categories: tuple[str, ...],
    *,
    category_selected: bool,
) -> str:
    if not category_selected:
        return (
            "当前只开放 set_tool_categories，必须先调用它选择完成用户最新消息所需的类别；"
            "在控制工具返回前不得直接回答、等待或声称工具不可用。调用后立即结束当前模型步骤。"
        )
    if not categories:
        return (
            "本轮已经通过 set_tool_categories 明确关闭全部业务工具。"
            "现在可以直接完成不需要业务工具的回复；如需业务能力，重新调用 set_tool_categories，"
            "调用后立即结束当前模型步骤。"
        )
    labels = "、".join(TOOL_CATEGORY_METADATA[category]["label"] for category in categories)
    return (
        f"同一用户回合的上一模型步骤已经选定类别，当前开放工具类别：{labels}。"
        "本步骤直接调用已经开放的业务工具完成用户最新任务，不必再次选择相同类别；"
        "需要更换能力时调用 set_tool_categories，"
        "调用后立即结束当前模型步骤。"
    )


def assistant_title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")
