"""Failure policy and model probing for managed OpenCode activation."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.ai.local_cli_adapter import (
    DEFAULT_CLI_ARGS,
    LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    LocalCLIAdapter,
)
from app.architecture.uow import commit_session
from app.services.model_readiness import (
    mark_model_failure,
    mark_model_ready,
    mark_model_unavailable,
)


def activation_failure_kind(message: str, *, context: str | None = None) -> str:
    value = message.lower()
    if any(
        token in value
        for token in (
            "certificate_verify_failed",
            "certificate verify failed",
            "unable to get local issuer certificate",
            "self-signed certificate",
            "self signed certificate",
            "证书验证失败",
            "证书链",
        )
    ):
        return "certificate_verification"
    if any(token in value for token in ("auth", "login", "unauthorized", "登录", "凭据")):
        return "authentication_required"
    rate_limited = any(
        token in value
        for token in (
            "quota",
            "rate limit",
            "rate_limit",
            "free usage",
            "http error 403",
            "http error 429",
            "status code 403",
            "status code 429",
            "status=403",
            "status=429",
            "额度",
            "限流",
        )
    )
    if rate_limited and context == "download":
        return "download_rate_limit"
    if rate_limited:
        return "quota_or_rate_limit"
    if any(token in value for token in ("disk", "space", "磁盘", "空间不足")):
        return "disk_space"
    if any(token in value for token in ("permission", "access is denied", "权限", "拒绝访问")):
        return "permission_or_antivirus"
    if any(
        token in value
        for token in ("timed out", "timeout", "network", "urlopen", "http error", "网络")
    ):
        return "network"
    return "runtime"


def save_readiness_failure(message: str, *, unavailable_fallback: bool = False) -> None:
    from app.database.models import APIConfig
    from app.database.session import SessionLocal

    try:
        with SessionLocal() as db:
            config = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
            if config:
                changed = mark_model_failure(config, message, source="opencode_activation")
                if not changed and unavailable_fallback:
                    mark_model_unavailable(config, message, source="opencode_activation")
                    changed = True
                if changed:
                    commit_session(db)
    except Exception:
        return


async def test_model(command: str, model: str, *, timeout_seconds: int) -> None:
    from app.services.content_store import content_root

    adapter = LocalCLIAdapter(
        api_key="",
        base_url="opencode_cli",
        cli_command=command,
        cli_args=json.dumps(DEFAULT_CLI_ARGS["opencode_cli"], ensure_ascii=False),
    )
    result = await asyncio.wait_for(
        adapter.chat_completion(
            messages=[
                {"role": "system", "content": "你是连接测试执行器。"},
                {"role": "user", "content": "只回复：连接成功"},
            ],
            model=model,
            temperature=0,
            max_tokens=32,
            extra_body={
                "local_cli_cwd": str(content_root()),
                "local_cli_timeout_seconds": timeout_seconds,
            },
        ),
        timeout=timeout_seconds + LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    )
    reply = str(result.get("content") or "").strip()
    if "连接成功" not in reply:
        raise RuntimeError(f"模型返回了无法识别的测试结果：{reply[:160] or '空响应'}")


def save_activated_config(command: str, model: str) -> None:
    from app.core.crypto import encrypt
    from app.database.models import APIConfig
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        config = db.query(APIConfig).filter(APIConfig.provider == "opencode_cli").first()
        if not config:
            config = APIConfig(
                provider="opencode_cli",
                api_key_encrypted=encrypt("__local_cli__"),
                default_model=model,
                provider_type="local_cli",
            )
            db.add(config)
        config.api_key_encrypted = encrypt("__local_cli__")
        config.default_model = model
        config.provider_type = "local_cli"
        config.cli_command = command
        config.cli_args = json.dumps(DEFAULT_CLI_ARGS["opencode_cli"], ensure_ascii=False)
        mark_model_ready(config, source="opencode_activation")
        db.query(APIConfig).update({"is_global_default": False})
        config.is_global_default = True
        commit_session(db)


@dataclass
class FreeModelProbeResult:
    selected_model: str | None
    failures: list[tuple[str, str, str]]
    model_results: list[dict[str, Any]]


def probe_free_models(
    *,
    job_id: str,
    command: str,
    ordered: list[dict[str, Any]],
    update_activation: Callable[..., dict[str, Any]],
    test_model_call: Callable[[str, str], Any],
) -> FreeModelProbeResult:
    failures: list[tuple[str, str, str]] = []
    model_results = [{**item, "test_status": "untested"} for item in ordered]
    for index, option in enumerate(ordered):
        model = str(option.get("id") or "")
        previous_failure = failures[-1] if failures else None
        testing_message = f"正在测试免费模型：{model}"
        if previous_failure:
            testing_message = (
                f"{previous_failure[0]} 当前不可用（{previous_failure[1]}），"
                f"已切换测试：{model}"
            )
        current = next(item for item in model_results if item.get("id") == model)
        current["test_status"] = "testing"
        current.pop("failure_kind", None)
        update_activation(
            job_id,
            phase="testing",
            percent=min(98, 90 + index * 2),
            message=testing_message,
            selected_model=model,
            free_models_json=deepcopy(model_results),
        )
        try:
            asyncio.run(test_model_call(command, model))
            current["test_status"] = "ready"
            return FreeModelProbeResult(model, failures, model_results)
        except Exception as exc:
            message = str(getattr(exc, "message", None) or exc)
            kind = activation_failure_kind(message, context="model")
            failures.append((model, kind, message))
            current["test_status"] = "rate_limited" if kind == "quota_or_rate_limit" else "failed"
            current["failure_kind"] = kind
            update_activation(job_id, free_models_json=deepcopy(model_results))
            if kind == "authentication_required":
                break
    return FreeModelProbeResult(None, failures, model_results)
