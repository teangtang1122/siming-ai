"""Shared failure classification for operations and model readiness."""

from __future__ import annotations

import re


def classify_failure(message: str | None) -> str | None:
    text = str(message or "").strip()
    if not text:
        return None
    lower = text.lower()
    if re.search(r"free\s+usage\s+exceeded|quota|rate\s*limit|too many requests|429|402", lower):
        return "quota_or_rate_limit"
    if re.search(
        r"invalidtoken|invalid[_\s-]*token|expired[_\s-]*token|401|unauthori[sz]ed|login|required",
        lower,
    ):
        return "auth"
    if "timeout" in lower or "超时" in text:
        return "timeout"
    if re.search(
        r"cannot connect|connection (?:failed|refused|reset)|network error|网络连接|无法连接",
        lower + " " + text,
    ):
        return "network"
    if re.search(r"unavailable|not available|executable.*not found|command.*not found", lower):
        return "unavailable"
    if "没有收到模型的文字回复" in text or "empty response" in lower or "no text" in lower:
        return "empty_response"
    if (
        re.search(r"invalid\s+(?:json|response)|json.*(?:parse|format)|cannot parse", lower)
        or "格式无法解析" in text
        or ("json" in lower and "格式" in text)
    ):
        return "invalid_response"
    if (
        "only `read` tool" in lower
        or "工具均未注册" in text
        or ("tool" in lower and "not registered" in lower)
    ):
        return "tool_unavailable"
    if "未入库" in text or "orphan" in lower or ("mirror" in lower and "database" in lower):
        return "storage_contract"
    return "unknown"
