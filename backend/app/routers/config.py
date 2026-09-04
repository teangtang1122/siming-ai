"""Model config CRUD, global default model, and compatibility chat endpoints."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ..ai.local_cli_adapter import (
    DEFAULT_CLI_ARGS,
    DEFAULT_CLI_COMMANDS,
    is_local_cli_provider,
    local_cli_model_options,
)
from ..ai.local_runtime_policy import local_runtime_disabled, local_runtime_disabled_message
from ..core.config import get_settings
from ..core.crypto import decrypt, encrypt
from ..core.exceptions import AppException, LLMError, NotFoundError, ValidationError
from ..core.model_capacity_catalog import uses_documented_model_catalog
from ..core.model_limits import limits_payload
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..modules.model_runtime.application.verification import (
    ModelProbeRequest,
    get_model_verification,
)
from ..modules.model_runtime.interfaces.config_dependencies import model_config_crud
from ..schemas.config import (
    TASK_MODEL_TYPES,
    APIConfigCreate,
    ConnectionTestRequest,
    GlobalModelSetting,
    ModelListRequest,
    TaskModelSettingUpdate,
)
from ..services.content_store import content_root as resolve_content_root
from ..services.external_agent.mcp_auto_config import (
    configure_cli_integration,
    restore_cli_integration,
    scan_cli_integrations,
)
from ..services.model_config_options import (
    DEEPSEEK_SUPPORTED_MODELS,
)
from ..services.model_config_options import (
    enriched_model_options as _enriched_model_options,
)
from ..services.model_config_options import (
    normalize_model_for_provider as _normalize_model_for_provider,
)
from ..services.model_config_options import (
    normalized_model_options as _normalized_model_options,
)
from ..services.model_context_profiles import (
    configured_model_context_profile,
    save_model_context_profile,
)
from ..services.model_readiness import (
    is_model_config_usable,
    mark_model_ready,
    mark_model_testing,
    mark_model_unverified,
    mark_model_verification_failure,
    mark_model_verification_unavailable,
    readiness_payload,
)
from ..version import APP_VERSION
from .application_updates import (
    LauncherSettingsUpdateRequest,  # noqa: F401 - compatibility export
    update_launcher_settings,  # noqa: F401 - compatibility export
)
from .config_storage import router as storage_router

router = APIRouter(tags=["config"])
router.include_router(storage_router)


@router.get("/config/app-info")
def get_app_info():
    """Return local build identity without performing a network update check."""
    return ApiResponse.success(data={"name": "Siming", "version": APP_VERSION})


class ChatCompletionRequest(BaseModel):
    """OpenAI-style chat completion request for compatibility/testing."""

    messages: list[dict] = Field(..., min_length=1)
    model: str | None = None
    temperature: float = Field(0.7, ge=0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    extra_body: dict | None = None


PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "deepseek": "DeepSeek",
    "qwen": "Tongyi Qianwen",
    "gemini": "Google Gemini",
    "claude_cli": "Claude Code CLI",
    "codex_cli": "Codex CLI",
    "opencode_cli": "opencode CLI",
    "mimocode_cli": "MiMo Code CLI",
    "cursor_cli": "Cursor Agent CLI",
    "kilocode_cli": "Kilo Code CLI",
    "qwen_code_cli": "Qwen Code CLI",
    "hermes_cli": "Hermes Agent CLI",
    "openclaw_cli": "OpenClaw CLI",
    "dsh_cli": "DeepSeek Harness CLI",
    "custom_cli": "Custom Local CLI",
    "local_llama_cpp": "Siming Local AI",
}

LOCAL_CLI_PROVIDER_TYPE = "local_cli"
LOCAL_CLI_PLACEHOLDER_KEY = "__local_cli__"
LOCAL_RUNTIME_PROVIDER_TYPE = "local_runtime"
LOCAL_RUNTIME_PLACEHOLDER_KEY = "__local_runtime__"
API_PROTOCOL_AUTO = "auto"
API_PROTOCOL_CHAT = "chat_completions"
API_PROTOCOL_RESPONSES = "responses"


def _mask_key(key: str) -> str:
    if len(key) <= 12:
        return "****"
    return key[:4] + "****" + key[-4:]


def _provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider)


def _normalize_provider_type(provider: str, provider_type: str | None = None) -> str:
    if provider_type:
        return provider_type
    if provider == "local_llama_cpp":
        return LOCAL_RUNTIME_PROVIDER_TYPE
    return LOCAL_CLI_PROVIDER_TYPE if is_local_cli_provider(provider) else "api"


def _require_provider_available(provider: str, provider_type: str | None = None) -> None:
    if not get_settings().local_runtime_enabled and (
        provider == "local_llama_cpp"
        or is_local_cli_provider(provider)
        or provider_type in {LOCAL_CLI_PROVIDER_TYPE, LOCAL_RUNTIME_PROVIDER_TYPE}
    ):
        raise ValidationError(
            "Docker Gateway 仅支持云端 API 模型；本地模型与 CLI 请在司命桌面端使用。"
        )


def _default_cli_command(provider: str) -> str | None:
    return DEFAULT_CLI_COMMANDS.get(provider) or None


def _default_cli_args(provider: str) -> str | None:
    args = DEFAULT_CLI_ARGS.get(provider)
    return json.dumps(args, ensure_ascii=False) if args else None


def _resolve_base_url(provider: str, base_url_override: str | None) -> str:
    if is_local_cli_provider(provider) or provider == "local_llama_cpp":
        return ""
    if base_url_override:
        return base_url_override.rstrip("/")
    if provider not in PROVIDER_DEFAULT_BASE_URLS:
        raise ValidationError("自定义 OpenAI 兼容提供商必须填写自定义 API 端点")
    return PROVIDER_DEFAULT_BASE_URLS[provider]


def _is_custom_api_provider(provider: str) -> bool:
    return provider not in PROVIDER_DEFAULT_BASE_URLS and not is_local_cli_provider(provider)


def _protocol_label(protocol: str) -> str:
    return "Responses API" if protocol == API_PROTOCOL_RESPONSES else "Chat Completions"


def _normalize_model_list_for_provider(
    provider: str,
    models: list[dict],
    db: Session | None = None,
) -> list[dict]:
    if is_local_cli_provider(provider):
        return models or local_cli_model_options(provider)
    if provider == "local_llama_cpp":
        from ..services.local_runtime.model_jobs import ensure_catalog_rows

        ensure_catalog_rows()
        if db is None:
            raise RuntimeError("A request session is required for the local model catalog")
        return [
            {"id": item.model_key, "display_name": item.display_name}
            for item in model_config_crud(db).list_local_models()
        ]
    if provider == "gemini":
        normalized: dict[str, dict] = {}
        for model in models:
            model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
            if model_id:
                normalized[model_id] = {
                    **model,
                    "id": model_id,
                    "display_name": model.get("display_name") or model_id,
                }
        return list(normalized.values())
    if provider != "deepseek":
        return models
    normalized = {}
    for model in models:
        model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
        if model_id in DEEPSEEK_SUPPORTED_MODELS:
            normalized[model_id] = {
                **model,
                "id": model_id,
                "display_name": model.get("display_name") or model_id,
            }
    return list(normalized.values()) or [
        {"id": model_id, "display_name": model_id}
        for model_id in sorted(DEEPSEEK_SUPPORTED_MODELS)
    ]


def _available_model_options(cfg: Any, db: Session | None = None) -> list[dict[str, Any]]:
    models = list(getattr(cfg, "available_models_json", None) or [])
    if cfg.provider == "local_llama_cpp" and db is not None:
        models = [
            {"id": item.model_key, "display_name": item.display_name}
            for item in model_config_crud(db).list_local_models()
            if item.status == "installed"
        ]
    return _normalized_model_options(
        cfg.provider,
        models,
        default_model=cfg.default_model,
        use_documented_catalog=uses_documented_model_catalog(
            cfg.provider,
            getattr(cfg, "base_url_override", None),
        ),
    )


def _default_model_capacity(cfg: Any, db: Session | None) -> dict[str, Any]:
    provider = str(cfg.provider)
    model_name = _normalize_model_for_provider(
        provider,
        str(cfg.default_model),
        strict=False,
    )
    if db is not None:
        profile = configured_model_context_profile(
            db,
            provider=provider,
            model_name=model_name,
        )
        if profile is not None:
            return {
                "context_window_tokens": int(profile.context_window_tokens),
                "context_safety_margin_tokens": int(profile.safety_margin_tokens),
                "context_profile_source": "configured",
                "context_profile_known": True,
            }
    for option in _available_model_options(cfg, db):
        if option.get("id") != model_name or not option.get("context_window_tokens"):
            continue
        return {
            "context_window_tokens": int(option["context_window_tokens"]),
            "context_safety_margin_tokens": int(
                option.get("safety_margin_tokens") or 512
            ),
            "context_profile_source": str(
                option.get("capacity_source") or "provider_metadata"
            ),
            "context_profile_known": True,
        }
    return {
        "context_window_tokens": None,
        "context_safety_margin_tokens": 512,
        "context_profile_source": None,
        "context_profile_known": False,
    }


def _save_default_model_capacity(
    db: Session,
    *,
    provider: str,
    model_name: str,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    safety_margin_tokens: int | None,
) -> None:
    save_model_context_profile(
        db,
        provider=provider,
        model_name=model_name,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        safety_margin_tokens=safety_margin_tokens,
    )


def _config_payload(
    cfg: Any,
    include_masked_key: bool = False,
    *,
    db: Session | None = None,
) -> dict:
    default_model = _normalize_model_for_provider(cfg.provider, cfg.default_model, strict=False)
    data = {
        "id": cfg.id,
        "provider": cfg.provider,
        "default_model": default_model,
        "is_global_default": cfg.is_global_default,
        "base_url_override": cfg.base_url_override,
        "api_protocol": getattr(cfg, "api_protocol", None) or API_PROTOCOL_AUTO,
        "provider_type": getattr(cfg, "provider_type", None) or _normalize_provider_type(cfg.provider),
        "cli_command": getattr(cfg, "cli_command", None),
        "cli_args": getattr(cfg, "cli_args", None),
        "available_models": _available_model_options(cfg, db),
        "api_key_configured": bool(getattr(cfg, "api_key_encrypted", None)),
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }
    data.update(limits_payload(
        cfg.provider,
        default_model,
        max_output_tokens=cfg.max_output_tokens,
        deconstruct_input_char_limit=cfg.deconstruct_input_char_limit,
        deconstruct_item_char_limit=cfg.deconstruct_item_char_limit,
    ))
    data.update(_default_model_capacity(cfg, db))
    data.update(readiness_payload(cfg))
    if include_masked_key:
        if is_local_cli_provider(cfg.provider):
            data["api_key_masked"] = "Local CLI"
        elif cfg.provider == "local_llama_cpp":
            data["api_key_masked"] = "Local runtime"
        else:
            masked = "not configured"
            try:
                masked = _mask_key(decrypt(cfg.api_key_encrypted))
            except Exception:
                pass
            data["api_key_masked"] = masked
    return data


def _validate_cli_command(command: str | None) -> str:
    command = (command or "").strip()
    if not command:
        raise ValidationError("Local CLI command is required")
    if not (shutil.which(command) or Path(command).exists()):
        raise ValidationError(f"Local CLI command not found: {command}")
    return command


@router.get("/config/models")
def list_model_configs(db: Session = Depends(get_db)):
    configs = model_config_crud(db).list_configs()
    items = [
        _config_payload(cfg, db=db)
        for cfg in configs
        if not local_runtime_disabled(cfg.provider)
        and (
            get_settings().local_runtime_enabled
            or (
                not is_local_cli_provider(cfg.provider)
                and cfg.provider != "local_llama_cpp"
            )
        )
    ]
    task_models = {
        setting.task_type: {
            "task_type": setting.task_type,
            "provider": setting.provider,
            "model": _normalize_model_for_provider(
                setting.provider,
                setting.model_name,
                strict=False,
            ),
            "context_length": setting.context_length,
        }
        for setting in model_config_crud(db).list_task_settings()
    }
    return ApiResponse.success(
        data={"items": items, "total": len(items), "task_models": task_models}
    )


def _require_task_model_type(task_type: str) -> str:
    normalized = str(task_type or "").strip()
    if normalized not in TASK_MODEL_TYPES:
        raise ValidationError(
            "不支持的任务类型；可选值为 " + ", ".join(TASK_MODEL_TYPES)
        )
    return normalized


def _task_model_payload(setting: Any, db: Session) -> dict[str, Any]:
    config = model_config_crud(db).get_provider(setting.provider)
    return {
        "task_type": setting.task_type,
        "provider": setting.provider,
        "model": _normalize_model_for_provider(
            setting.provider,
            setting.model_name,
            strict=False,
        ),
        "context_length": setting.context_length,
        "is_usable": bool(config and is_model_config_usable(config)),
    }


@router.get("/config/task-models")
def list_task_models(db: Session = Depends(get_db)):
    items = [
        _task_model_payload(setting, db)
        for setting in model_config_crud(db).list_task_settings()
    ]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.put("/config/task-models/{task_type}")
def set_task_model(
    task_type: str,
    payload: TaskModelSettingUpdate,
    db: Session = Depends(get_db),
):
    task_type = _require_task_model_type(task_type)
    _require_provider_available(payload.provider)
    if local_runtime_disabled(payload.provider):
        raise ValidationError(local_runtime_disabled_message())

    crud = model_config_crud(db)
    config = crud.get_provider(payload.provider)
    if not config or not is_model_config_usable(config):
        raise ValidationError("所选提供商尚未通过真实对话测试，请先测试并启用")

    model_name = _normalize_model_for_provider(payload.provider, payload.model)
    available_ids = {
        option["id"] for option in _available_model_options(config, db)
    }
    if model_name not in available_ids:
        raise ValidationError("所选模型不在该提供商已获取的模型列表中，请先刷新模型列表")

    context_length = payload.context_length
    if payload.provider == "local_llama_cpp":
        local_model = next(
            (
                item
                for item in crud.list_local_models()
                if item.model_key == model_name and item.status == "installed"
            ),
            None,
        )
        if local_model is None:
            raise ValidationError("所选本地模型尚未安装")
        if context_length and context_length > int(local_model.context_length or 0):
            raise ValidationError(
                f"任务上下文 {context_length} 超过模型容量 {local_model.context_length}"
            )
    elif context_length is not None:
        raise ValidationError("上下文启动长度只适用于司命本地模型")

    setting = crud.get_task_setting(task_type)
    if setting is None:
        setting = crud.create_task_setting(
            task_type=task_type,
            provider=payload.provider,
            model_name=model_name,
            context_length=context_length,
            adapter_ids=[],
        )
    else:
        changed_model = (
            setting.provider != payload.provider or setting.model_name != model_name
        )
        setting.provider = payload.provider
        setting.model_name = model_name
        setting.context_length = context_length
        if changed_model:
            setting.adapter_ids = []
    commit_session(db)
    db.refresh(setting)
    return ApiResponse.success(
        data=_task_model_payload(setting, db),
        message="任务默认模型已保存",
    )


@router.delete("/config/task-models/{task_type}")
def clear_task_model(task_type: str, db: Session = Depends(get_db)):
    task_type = _require_task_model_type(task_type)
    crud = model_config_crud(db)
    setting = crud.get_task_setting(task_type)
    if setting is not None:
        crud.delete(setting)
        commit_session(db)
    return ApiResponse.success(message="任务默认模型已清除，将跟随全局默认模型")


@router.post("/config/models")
def create_or_update_model_config(payload: APIConfigCreate, db: Session = Depends(get_db)):
    """Add or update an API or local CLI config."""

    provider_type = _normalize_provider_type(payload.provider, payload.provider_type)
    _require_provider_available(payload.provider, provider_type)
    is_cli = provider_type == LOCAL_CLI_PROVIDER_TYPE or is_local_cli_provider(payload.provider)
    is_runtime = provider_type == LOCAL_RUNTIME_PROVIDER_TYPE or payload.provider == "local_llama_cpp"
    crud = model_config_crud(db)
    existing = crud.get_provider(payload.provider)
    if is_runtime and local_runtime_disabled(payload.provider):
        raise ValidationError(local_runtime_disabled_message())

    if is_cli:
        api_key = LOCAL_CLI_PLACEHOLDER_KEY
        base_url_override = None
        cli_command = (payload.cli_command or _default_cli_command(payload.provider) or "").strip()
        if payload.provider != "custom_cli":
            # Built-in CLI providers can be saved before installation so users
            # can configure first; connection test does the executable check.
            cli_command = cli_command or payload.provider.removesuffix("_cli")
        else:
            _validate_cli_command(cli_command)
        cli_args = payload.cli_args or _default_cli_args(payload.provider)
    elif is_runtime:
        api_key = LOCAL_RUNTIME_PLACEHOLDER_KEY
        base_url_override = None
        cli_command = None
        cli_args = None
    else:
        if payload.api_key:
            api_key = payload.api_key
        elif existing:
            api_key = decrypt(existing.api_key_encrypted)
        else:
            raise ValidationError("API Key is required for API providers")
        base_url_override = (
            payload.base_url_override
            if payload.base_url_override is not None
            else getattr(existing, "base_url_override", None)
        )
        _resolve_base_url(payload.provider, base_url_override)
        cli_command = None
        cli_args = None

    default_model = _normalize_model_for_provider(payload.provider, payload.default_model)
    available_models = _normalized_model_options(
        payload.provider,
        payload.available_models,
        default_model=default_model,
        use_documented_catalog=uses_documented_model_catalog(
            payload.provider,
            base_url_override,
        ),
    )
    encrypted_key = encrypt(api_key)

    if existing:
        existing.api_key_encrypted = encrypted_key
        existing.default_model = default_model
        existing.provider_type = LOCAL_CLI_PROVIDER_TYPE if is_cli else LOCAL_RUNTIME_PROVIDER_TYPE if is_runtime else "api"
        existing.base_url_override = base_url_override
        existing.api_protocol = API_PROTOCOL_CHAT if is_cli or is_runtime else payload.api_protocol
        existing.cli_command = cli_command
        existing.cli_args = cli_args
        existing.max_output_tokens = payload.max_output_tokens
        existing.deconstruct_input_char_limit = payload.deconstruct_input_char_limit
        existing.deconstruct_item_char_limit = payload.deconstruct_item_char_limit
        existing.available_models_json = available_models
        mark_model_unverified(existing, source="manual_edit")
        _save_default_model_capacity(
            db,
            provider=payload.provider,
            model_name=default_model,
            context_window_tokens=payload.context_window_tokens,
            max_output_tokens=payload.max_output_tokens,
            safety_margin_tokens=payload.context_safety_margin_tokens,
        )
        commit_session(db)
        db.refresh(existing)
        return ApiResponse.success(
            data=_config_payload(existing, db=db),
            message=f"{payload.provider} 配置已更新",
        )

    config = crud.create(
        provider=payload.provider,
        api_key_encrypted=encrypted_key,
        default_model=default_model,
        provider_type=LOCAL_CLI_PROVIDER_TYPE if is_cli else LOCAL_RUNTIME_PROVIDER_TYPE if is_runtime else "api",
        base_url_override=base_url_override,
        api_protocol=API_PROTOCOL_CHAT if is_cli or is_runtime else payload.api_protocol,
        cli_command=cli_command,
        cli_args=cli_args,
        readiness_status="unverified",
        readiness_json='{"source":"manual"}',
        max_output_tokens=payload.max_output_tokens,
        deconstruct_input_char_limit=payload.deconstruct_input_char_limit,
        deconstruct_item_char_limit=payload.deconstruct_item_char_limit,
        available_models_json=available_models,
    )
    _save_default_model_capacity(
        db,
        provider=payload.provider,
        model_name=default_model,
        context_window_tokens=payload.context_window_tokens,
        max_output_tokens=payload.max_output_tokens,
        safety_margin_tokens=payload.context_safety_margin_tokens,
    )
    commit_session(db)
    db.refresh(config)
    return ApiResponse.success(
        data=_config_payload(config, db=db),
        message=f"{payload.provider} 配置已添加",
    )


@router.post("/config/cli-integrations/scan")
def scan_local_cli_integrations():
    """Discover supported CLIs only after the author clicks Scan."""

    return ApiResponse.success(
        data=scan_cli_integrations(),
        message="本机 CLI 扫描完成；尚未修改任何配置",
    )


@router.post("/config/cli-integrations/{provider}/configure")
def configure_local_cli_integration(provider: str, db: Session = Depends(get_db)):
    """Apply one explicitly authorized CLI integration."""

    saved = model_config_crud(db).get_provider(provider)
    result = configure_cli_integration(
        provider,
        cli_command=getattr(saved, "cli_command", None) if saved else None,
        permission_pack="auto",
    )
    return ApiResponse.success(data=result, message=result.get("detail") or "CLI 配置已处理")


@router.post("/config/cli-integrations/{provider}/restore")
def restore_local_cli_integration(provider: str):
    """Restore one CLI only from an explicit, conflict-checked snapshot."""

    result = restore_cli_integration(provider)
    return ApiResponse.success(data=result, message=result.get("detail") or "CLI 还原已处理")


@router.get("/config/models/{provider}")
def get_model_config_detail(provider: str, db: Session = Depends(get_db)):
    _require_provider_available(provider)
    if local_runtime_disabled(provider):
        raise ValidationError(local_runtime_disabled_message())
    config = model_config_crud(db).get_provider(provider)
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")
    return ApiResponse.success(data=_config_payload(config, include_masked_key=True, db=db))


@router.post("/config/models/list")
async def list_provider_models(payload: ModelListRequest, db: Session = Depends(get_db)):
    _require_provider_available(payload.provider)
    if local_runtime_disabled(payload.provider):
        raise ValidationError(local_runtime_disabled_message())
    warning = None
    manual_entry_required = False
    saved = model_config_crud(db).get_provider(payload.provider) if hasattr(db, "query") else None
    api_key = payload.api_key
    if not is_local_cli_provider(payload.provider) and payload.provider != "local_llama_cpp" and not api_key:
        if not saved:
            raise ValidationError("API Key is required")
        api_key = decrypt(saved.api_key_encrypted)
    base_url = ""
    if payload.provider != "local_llama_cpp" and not is_local_cli_provider(payload.provider):
        base_url = _resolve_base_url(
            payload.provider,
            payload.base_url_override if payload.base_url_override is not None else getattr(saved, "base_url_override", None),
        )
    request = ModelProbeRequest(
        provider=payload.provider,
        api_key=api_key or "",
        base_url=base_url,
        cli_command=payload.cli_command or getattr(saved, "cli_command", None),
        cli_args=payload.cli_args or getattr(saved, "cli_args", None),
    )
    try:
        models = await get_model_verification().list_models(request)
    except LLMError as exc:
        if _is_custom_api_provider(payload.provider) and any(
            marker in str(exc).lower() for marker in ("404", "405", "not found", "method not allowed")
        ):
            models = []
            manual_entry_required = True
            warning = "该接口未提供模型列表，请手动填写服务商支持的模型名；这不代表模型不可用"
        else:
            raise

    if _is_custom_api_provider(payload.provider) and not models and not manual_entry_required:
        manual_entry_required = True
        warning = "该接口返回了空模型列表，请手动填写服务商支持的模型名"

    models = _enriched_model_options(
        payload.provider,
        _normalize_model_list_for_provider(payload.provider, models, db),
        use_documented_catalog=uses_documented_model_catalog(
            payload.provider,
            base_url or None,
        ),
    )
    if saved:
        same_saved_connection = (
            not payload.api_key
            and (
                payload.base_url_override is None
                or (payload.base_url_override or "").rstrip("/")
                == (getattr(saved, "base_url_override", None) or "").rstrip("/")
            )
            and (
                not payload.cli_command
                or payload.cli_command == getattr(saved, "cli_command", None)
            )
            and (
                not payload.cli_args
                or payload.cli_args == getattr(saved, "cli_args", None)
            )
        )
        if same_saved_connection:
            saved.available_models_json = _normalized_model_options(
                payload.provider,
                models,
                default_model=saved.default_model,
                use_documented_catalog=uses_documented_model_catalog(
                    payload.provider,
                    base_url or None,
                ),
            )
            commit_session(db)
    return ApiResponse.success(
        data={
            "models": models,
            "manual_entry_required": manual_entry_required,
            "warning": warning,
        },
        message=warning or f"Fetched {len(models)} models",
    )


@router.post("/config/models/test")
async def test_connection(payload: ConnectionTestRequest, db: Session = Depends(get_db)):
    _require_provider_available(payload.provider)
    if local_runtime_disabled(payload.provider):
        raise ValidationError(local_runtime_disabled_message())
    is_cli = is_local_cli_provider(payload.provider)
    is_runtime = payload.provider == "local_llama_cpp"
    saved = model_config_crud(db).get_provider(payload.provider) if hasattr(db, "query") else None
    command = payload.cli_command or getattr(saved, "cli_command", None) or _default_cli_command(payload.provider) if is_cli else None
    if is_cli:
        _validate_cli_command(command)
    api_key = payload.api_key
    if not is_cli and not is_runtime and not api_key:
        if not saved:
            raise ValidationError("API Key is required")
        api_key = decrypt(saved.api_key_encrypted)
    model = (payload.model or "").strip()
    if not model:
        raise ValidationError("请先填写要实际调用的模型名")
    base_url = "" if is_cli or is_runtime else _resolve_base_url(
        payload.provider,
        payload.base_url_override if payload.base_url_override is not None else getattr(saved, "base_url_override", None),
    )
    test_data = await get_model_verification().verify(
        ModelProbeRequest(
            provider=payload.provider,
            model=model,
            api_key=api_key or "",
            base_url=base_url,
            api_protocol=payload.api_protocol,
            cli_command=command,
            cli_args=payload.cli_args or getattr(saved, "cli_args", None) or (_default_cli_args(payload.provider) if is_cli else None),
            timeout_seconds=payload.timeout_seconds,
            content_root=resolve_content_root(),
        )
    )
    if is_cli or is_runtime:
        message = f"{_provider_label(payload.provider)} real conversation succeeded"
    else:
        message = (
            f"{_provider_label(payload.provider)} real conversation succeeded "
            f"({_protocol_label(test_data['api_protocol'])})"
        )
    return ApiResponse.success(
        data=test_data,
        message=message,
    )


@router.post("/config/models/{provider}/verify")
async def verify_saved_model_config(provider: str, db: Session = Depends(get_db)):
    """Run a real saved-config test and persist whether the model is usable."""

    _require_provider_available(provider)
    if local_runtime_disabled(provider):
        raise ValidationError(local_runtime_disabled_message())
    crud = model_config_crud(db)
    config = crud.get_provider(provider)
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")

    mark_model_testing(config)
    commit_session(db)
    db.refresh(config)

    is_cli = is_local_cli_provider(provider)
    is_runtime = provider == "local_llama_cpp"
    payload = ConnectionTestRequest(
        provider=provider,
        api_key=None if is_cli or is_runtime else decrypt(config.api_key_encrypted),
        base_url_override=config.base_url_override,
        api_protocol=getattr(config, "api_protocol", None) or API_PROTOCOL_AUTO,
        cli_command=config.cli_command,
        cli_args=config.cli_args,
        model=config.default_model,
    )
    try:
        test_result = await test_connection(payload, db)
        if provider == "opencode_cli":
            # Writing also needs capacity; a short connection probe alone
            # cannot make a newly configured model ready for its first turn.
            models = await get_model_verification().list_models(ModelProbeRequest(
                provider=provider,
                api_key="",
                base_url="",
                cli_command=config.cli_command,
                cli_args=config.cli_args,
            ))
            config.available_models_json = _normalized_model_options(
                provider, models, default_model=config.default_model,
            )
    except Exception as exc:
        if not mark_model_verification_failure(config, exc, source="manual_verify"):
            mark_model_verification_unavailable(config, exc, source="manual_verify")
        commit_session(db)
        raise

    test_data = test_result.data or {}
    detected_protocol = str(test_data.get("api_protocol") or "").strip()
    if detected_protocol in {API_PROTOCOL_CHAT, API_PROTOCOL_RESPONSES} and not is_cli and not is_runtime:
        config.api_protocol = detected_protocol
    resolved_base_url = str(test_data.get("base_url") or "").strip()
    if resolved_base_url and config.base_url_override and resolved_base_url != config.base_url_override:
        config.base_url_override = resolved_base_url
    ready_message = None
    if detected_protocol:
        ready_message = (
            f"基础对话探测成功，使用 {_protocol_label(detected_protocol)}；"
            "长任务仍可能受到临时限流或服务容量影响"
        )
    mark_model_ready(config, source="manual_verify", message=ready_message)
    became_global = crud.make_global_if_no_ready_default(config)
    commit_session(db)
    db.refresh(config)
    return ApiResponse.success(
        data={
            "config": _config_payload(config, db=db),
            "test": test_result.data,
            "became_global_default": became_global,
        },
        message=(
            f"{_provider_label(provider)} 已完成基础对话探测并设为全局默认模型；"
            "长任务仍可能受到临时限流或服务容量影响"
            if became_global
            else f"{_provider_label(provider)} 已完成基础对话探测，可以发起创作；"
            "长任务仍可能受到临时限流或服务容量影响"
        ),
    )


@router.post("/chat/completion")
async def chat_completion(payload: ChatCompletionRequest):
    try:
        result = await LLMGateway.chat_completion(
            messages=payload.messages,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            extra_body=payload.extra_body,
        )
    except AppException as exc:
        raise LLMError(exc.message)
    return ApiResponse.success(data=result)


@router.post("/chat/completion/stream")
async def chat_completion_stream(payload: ChatCompletionRequest):
    async def _events():
        try:
            async for chunk in LLMGateway.stream_chat_completion(
                messages=payload.messages,
                model=payload.model,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
                extra_body=payload.extra_body,
            ):
                data = json.dumps({"type": "token", "content": chunk}, ensure_ascii=False, separators=(",", ":"))
                yield f"data: {data}\n\n"
        except AppException as exc:
            data = json.dumps({"type": "error", "message": exc.message}, ensure_ascii=False, separators=(",", ":"))
            yield f"data: {data}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.delete("/config/models/{provider}")
def delete_model_config(provider: str, db: Session = Depends(get_db)):
    crud = model_config_crud(db)
    config = crud.get_provider(provider)
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")
    crud.delete_task_settings_for_provider(provider)
    crud.delete(config)
    commit_session(db)
    return ApiResponse.success(message=f"{provider} 配置已删除")


@router.get("/config/global-model")
def get_global_model(db: Session = Depends(get_db)):
    config = model_config_crud(db).get_global()
    if (
        not config
        or not is_model_config_usable(config)
        or local_runtime_disabled(config.provider)
        or (
            not get_settings().local_runtime_enabled
            and (
                is_local_cli_provider(config.provider)
                or config.provider == "local_llama_cpp"
            )
        )
    ):
        return ApiResponse.success(data={"provider": None, "model": None}, message="未设置全局默认模型")
    return ApiResponse.success(data={
        "provider": config.provider,
        "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False),
    })


@router.put("/config/global-model")
def set_global_model(payload: GlobalModelSetting, db: Session = Depends(get_db)):
    _require_provider_available(payload.provider)
    if local_runtime_disabled(payload.provider):
        raise ValidationError(local_runtime_disabled_message())
    crud = model_config_crud(db)
    config = crud.get_provider(payload.provider)
    if not config:
        raise NotFoundError(f"未找到提供商 '{payload.provider}' 的配置，请先添加API配置")
    if not is_model_config_usable(config):
        raise ValidationError("这个模型尚未通过真实对话测试，请先点击“测试并启用”")

    crud.clear_global()
    config.is_global_default = True
    config.default_model = _normalize_model_for_provider(payload.provider, payload.model)
    commit_session(db)
    db.refresh(config)
    return ApiResponse.success(
        data={
            "provider": config.provider,
            "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False),
        },
        message=f"全局默认模型已设置为 {config.provider}:{config.default_model}",
    )
