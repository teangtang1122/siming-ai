"""Shared model readiness state and safe user-facing diagnostics."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.database.models import APIConfig
from app.services.observability.run_events import classify_failure


READINESS_DETECTED = "detected"
READINESS_UNVERIFIED = "unverified"
READINESS_TESTING = "testing"
READINESS_READY = "ready"
READINESS_AUTH_REQUIRED = "auth_required"
READINESS_QUOTA_LIMITED = "quota_limited"
READINESS_UNAVAILABLE = "unavailable"

READINESS_STATUSES = {
    READINESS_DETECTED,
    READINESS_UNVERIFIED,
    READINESS_TESTING,
    READINESS_READY,
    READINESS_AUTH_REQUIRED,
    READINESS_QUOTA_LIMITED,
    READINESS_UNAVAILABLE,
}

_STATUS_MESSAGES = {
    READINESS_DETECTED: "已在这台电脑上检测到，尚未验证登录、模型和额度",
    READINESS_UNVERIFIED: "配置已保存，测试成功后才能用于创作",
    READINESS_TESTING: "正在进行真实对话测试",
    READINESS_READY: "真实对话测试成功，可以用于创作",
    READINESS_AUTH_REQUIRED: "登录或凭据已经失效，请重新登录后验证",
    READINESS_QUOTA_LIMITED: "额度或速率限制已触发，请稍后重试或切换模型",
    READINESS_UNAVAILABLE: "当前连接不可用，请检查网络、模型和 CLI 状态",
}

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _details(config: APIConfig) -> dict[str, Any]:
    raw = getattr(config, "readiness_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sanitize_readiness_message(message: object, limit: int = 500) -> str:
    """Strip common credential shapes before persisting a diagnostic."""

    cleaned = str(message or "").replace("\x00", " ").strip()
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            cleaned = pattern.sub(r"\1[redacted]", cleaned)
        else:
            cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned[:limit]


def readiness_status(config: APIConfig) -> str:
    status = str(getattr(config, "readiness_status", "") or "").strip()
    return status if status in READINESS_STATUSES else READINESS_UNVERIFIED


def is_model_config_usable(config: APIConfig | None) -> bool:
    return bool(config and readiness_status(config) == READINESS_READY)


def readiness_payload(config: APIConfig) -> dict[str, Any]:
    status = readiness_status(config)
    details = _details(config)
    message = sanitize_readiness_message(details.get("message")) or _STATUS_MESSAGES[status]
    return {
        "readiness_status": status,
        "is_usable": status == READINESS_READY,
        "readiness_message": message,
        "readiness_source": details.get("source"),
        "failure_class": details.get("failure_class"),
        "last_tested_at": config.last_tested_at.isoformat() if getattr(config, "last_tested_at", None) else None,
    }


def set_model_readiness(
    config: APIConfig,
    status: str,
    *,
    source: str,
    message: object | None = None,
    failure_class: str | None = None,
    tested: bool = False,
) -> None:
    if status not in READINESS_STATUSES:
        raise ValueError(f"Unknown model readiness status: {status}")
    details = _details(config)
    details.update({"source": source})
    if message not in (None, ""):
        details["message"] = sanitize_readiness_message(message)
    else:
        details.pop("message", None)
    if failure_class:
        details["failure_class"] = failure_class
    else:
        details.pop("failure_class", None)
    if status == READINESS_READY:
        details["last_success_at"] = datetime.utcnow().isoformat()
    config.readiness_status = status
    config.readiness_json = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    if tested:
        config.last_tested_at = datetime.utcnow()


def mark_model_detected(config: APIConfig) -> None:
    set_model_readiness(config, READINESS_DETECTED, source="auto_detect")


def mark_model_unverified(config: APIConfig, *, source: str = "manual") -> None:
    set_model_readiness(config, READINESS_UNVERIFIED, source=source)
    config.last_tested_at = None
    config.is_global_default = False


def mark_model_testing(config: APIConfig, *, source: str = "manual_verify") -> None:
    set_model_readiness(config, READINESS_TESTING, source=source)


def mark_model_ready(config: APIConfig, *, source: str, message: object | None = None) -> None:
    set_model_readiness(
        config,
        READINESS_READY,
        source=source,
        message=message or _STATUS_MESSAGES[READINESS_READY],
        tested=True,
    )


def readiness_status_for_failure(message: object) -> tuple[str | None, str | None]:
    failure_class = classify_failure(sanitize_readiness_message(message, 2000))
    if failure_class == "auth":
        return READINESS_AUTH_REQUIRED, failure_class
    if failure_class == "quota_or_rate_limit":
        return READINESS_QUOTA_LIMITED, failure_class
    if failure_class in {"timeout", "empty_response", "network", "unavailable", "tool_unavailable"}:
        return READINESS_UNAVAILABLE, failure_class
    return None, failure_class


def mark_model_failure(config: APIConfig, error: BaseException | object, *, source: str) -> bool:
    status, failure_class = readiness_status_for_failure(error)
    if not status:
        return False
    set_model_readiness(
        config,
        status,
        source=source,
        message=error,
        failure_class=failure_class,
        tested=True,
    )
    config.is_global_default = False
    return True


def mark_model_unavailable(config: APIConfig, error: BaseException | object, *, source: str) -> None:
    """Persist a definitive verification failure that has no narrower class."""

    failure_class = classify_failure(sanitize_readiness_message(error, 2000)) or "unknown"
    set_model_readiness(
        config,
        READINESS_UNAVAILABLE,
        source=source,
        message=error,
        failure_class=failure_class,
        tested=True,
    )
    config.is_global_default = False


def record_gateway_failure(provider: str, error: BaseException | object) -> None:
    """Persist only failures that actually prove the selected model unusable."""

    from app.database.session import SessionLocal

    try:
        with SessionLocal() as db:
            config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
            if config and mark_model_failure(config, error, source="gateway"):
                db.commit()
    except Exception:
        # Readiness diagnostics must never replace the original model error.
        return
