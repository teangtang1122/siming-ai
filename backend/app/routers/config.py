"""Model config CRUD, global default model, and compatibility chat endpoints."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import webbrowser
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from openai import (
    APIConnectionError as OpenAIConnectionError,
    APIError as OpenAIAPIError,
    AuthenticationError as OpenAIAuthError,
    AsyncOpenAI,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..ai.local_cli_adapter import (
    LocalCLIAdapter,
    DEFAULT_CLI_ARGS,
    DEFAULT_CLI_COMMANDS,
    DEFAULT_CLI_MODELS,
    DEFAULT_LOCAL_CLI_TIMEOUT,
    LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    is_local_cli_provider,
    local_cli_model_options,
)
from ..core.crypto import decrypt, encrypt
from ..core.exceptions import AppException, LLMError, NotFoundError, ValidationError
from ..core.model_limits import limits_payload
from ..core.response import ApiResponse
from ..database.models import APIConfig
from ..database.session import get_db
from ..schemas.config import APIConfigCreate, ConnectionTestRequest, GlobalModelSetting, ModelListRequest
from ..services.content_store import content_root as resolve_content_root, migrate_projects_to_content_root
from ..services.external_agent.mcp_auto_config import auto_configure_mcp_for_provider

router = APIRouter(tags=["config"])


class ChatCompletionRequest(BaseModel):
    """OpenAI-style chat completion request for compatibility/testing."""

    messages: list[dict] = Field(..., min_length=1)
    model: str | None = None
    temperature: float = Field(0.7, ge=0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    extra_body: dict | None = None


class ContentRootUpdateRequest(BaseModel):
    path: str = Field(..., min_length=1)


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
    "custom_cli": "Custom Local CLI",
    "local_llama_cpp": "Siming Local AI",
}

DEEPSEEK_SUPPORTED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
DEEPSEEK_MODEL_ALIASES = {"deepseek-v3": "deepseek-v4-flash"}
LOCAL_CLI_PROVIDER_TYPE = "local_cli"
LOCAL_CLI_PLACEHOLDER_KEY = "__local_cli__"
LOCAL_RUNTIME_PROVIDER_TYPE = "local_runtime"
LOCAL_RUNTIME_PLACEHOLDER_KEY = "__local_runtime__"


def _app_home() -> Path:
    app_home = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME") or ""
    if app_home:
        return Path(app_home)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / "Siming"
    return Path.home() / "Siming"


def _launcher_settings_path() -> Path:
    return _app_home() / "launcher-settings.json"


def _load_launcher_settings() -> dict:
    path = _launcher_settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_launcher_settings(settings: dict) -> None:
    path = _launcher_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_content_root() -> Path:
    return (_app_home() / "projects").expanduser().resolve()


def _path_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    ignored = {".DS_Store", "Thumbs.db", "desktop.ini"}
    return not any(item.name not in ignored for item in path.iterdir())


def _looks_like_siming_content_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_dir() and (child / "moshu-project.json").exists():
            return True
    return False


def _content_root_payload(extra: dict | None = None) -> dict:
    settings = _load_launcher_settings()
    configured = settings.get("content_root")
    current = resolve_content_root()
    default = _default_content_root()
    looks_like_root = _looks_like_siming_content_root(current)
    payload = {
        "current_path": str(current),
        "configured_path": configured,
        "default_path": str(default),
        "is_default": not configured and current == default,
        "exists": current.exists(),
        "is_empty": _path_is_empty(current),
        "looks_like_siming_root": looks_like_root,
        "looks_like_moshu_root": looks_like_root,
    }
    if extra:
        payload.update(extra)
    return payload


def _apply_content_root(db: Session, raw_path: str) -> dict:
    target = Path(raw_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    current = resolve_content_root()
    if target != current and not _path_is_empty(target) and not _looks_like_siming_content_root(target):
        raise ValidationError("小说数据目录必须是空文件夹，或已经是 Siming 小说数据目录")
    settings = _load_launcher_settings()
    previous = current
    os.environ["SIMING_CONTENT_ROOT"] = str(target)
    os.environ["MOSHU_CONTENT_ROOT"] = str(target)
    settings["content_root"] = str(target)
    _save_launcher_settings(settings)
    migration = migrate_projects_to_content_root(db, target, previous_root=previous, cleanup_old=True)
    db.commit()
    return _content_root_payload({"migration": migration})


def _pick_empty_content_root() -> Path | None:
    try:
        import tkinter
        from tkinter import filedialog, messagebox

        root = tkinter.Tk()
        root.withdraw()
        while True:
            selected = filedialog.askdirectory(title="选择 Siming 小说数据目录")
            if not selected:
                root.destroy()
                return None
            path = Path(selected).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            if _path_is_empty(path) or _looks_like_siming_content_root(path):
                root.destroy()
                return path
            messagebox.showwarning(
                "Siming 小说数据目录",
                "请选择空目录，或已经由 Siming 创建过的小说数据目录。",
            )
    except Exception as exc:
        raise ValidationError(f"无法打开文件夹选择器：{exc}")


@router.post("/system/open-home")
def open_home_in_default_browser(request: Request):
    """Open the Siming web home in the user's default browser."""

    home_url = str(request.base_url).rstrip("/") + "/"
    webbrowser.open(home_url)
    return ApiResponse.success(data={"url": home_url}, message="Siming home opened in the default browser")


@router.get("/config/content-root")
def get_content_root_settings():
    """Return the current Siming 2.x novel data directory setting."""
    return ApiResponse.success(data=_content_root_payload())


@router.put("/config/content-root")
def update_content_root_settings(payload: ContentRootUpdateRequest, db: Session = Depends(get_db)):
    """Set the Siming novel data directory and migrate existing project files."""
    return ApiResponse.success(data=_apply_content_root(db, payload.path), message="小说数据目录已更新")


@router.post("/config/content-root/pick")
def pick_content_root_settings(db: Session = Depends(get_db)):
    """Open a native folder picker and set the selected Siming data directory."""
    selected = _pick_empty_content_root()
    if not selected:
        return ApiResponse.success(data=_content_root_payload({"cancelled": True}), message="已取消选择")
    return ApiResponse.success(data=_apply_content_root(db, str(selected)), message="小说数据目录已更新")


@router.get("/system/logs")
def get_system_logs(lines: int = 200):
    """Read the last N lines of the launcher log file."""

    log_path = _app_home() / "logs" / "launcher.log"
    if not log_path.exists():
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            legacy = Path(local_app_data) / "NovelWritingAgent" / "logs" / "launcher.log"
            if legacy.exists():
                log_path = legacy
    if not log_path.exists():
        return ApiResponse.success(data={"path": str(log_path), "content": "(log file not found)", "lines": 0})

    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return ApiResponse.success(data={
            "path": str(log_path),
            "content": "".join(tail),
            "lines": len(tail),
            "total": len(all_lines),
        })
    except Exception as exc:
        return ApiResponse.success(data={"path": str(log_path), "content": f"(read failed: {exc})", "lines": 0})


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


def _is_anthropic_provider(provider: str) -> bool:
    return provider == "anthropic"


def _normalize_model_for_provider(provider: str, model: str, *, strict: bool = True) -> str:
    if is_local_cli_provider(provider):
        return model or DEFAULT_CLI_MODELS.get(provider, f"{provider}-default")
    if provider == "local_llama_cpp":
        return model
    if provider == "gemini":
        return model.removeprefix("models/")
    if provider != "deepseek":
        return model
    normalized = DEEPSEEK_MODEL_ALIASES.get(model, model)
    if normalized not in DEEPSEEK_SUPPORTED_MODELS and strict:
        supported = ", ".join(sorted(DEEPSEEK_SUPPORTED_MODELS))
        raise ValidationError(f"DeepSeek currently supports: {supported}")
    return normalized


def _normalize_model_list_for_provider(provider: str, models: list[dict]) -> list[dict]:
    if is_local_cli_provider(provider):
        return models or local_cli_model_options(provider)
    if provider == "local_llama_cpp":
        from ..services.local_runtime.model_jobs import ensure_catalog_rows
        from ..database.session import SessionLocal
        from ..database.models import LocalModel

        ensure_catalog_rows()
        with SessionLocal() as db:
            return [
                {"id": item.model_key, "display_name": item.display_name}
                for item in db.query(LocalModel).order_by(LocalModel.recommended_vram_gb.asc()).all()
            ]
    if provider == "gemini":
        normalized: dict[str, dict] = {}
        for model in models:
            model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
            if model_id:
                normalized[model_id] = {"id": model_id, "display_name": model_id}
        return list(normalized.values())
    if provider != "deepseek":
        return models
    normalized = {}
    for model in models:
        model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
        if model_id in DEEPSEEK_SUPPORTED_MODELS:
            normalized[model_id] = {"id": model_id, "display_name": model_id}
    return list(normalized.values()) or [
        {"id": model_id, "display_name": model_id}
        for model_id in sorted(DEEPSEEK_SUPPORTED_MODELS)
    ]


def _config_payload(cfg: APIConfig, include_masked_key: bool = False) -> dict:
    default_model = _normalize_model_for_provider(cfg.provider, cfg.default_model, strict=False)
    data = {
        "id": cfg.id,
        "provider": cfg.provider,
        "default_model": default_model,
        "is_global_default": cfg.is_global_default,
        "base_url_override": cfg.base_url_override,
        "provider_type": getattr(cfg, "provider_type", None) or _normalize_provider_type(cfg.provider),
        "cli_command": getattr(cfg, "cli_command", None),
        "cli_args": getattr(cfg, "cli_args", None),
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


def _config_payload_with_mcp_setup(cfg: APIConfig, *, is_cli: bool) -> dict:
    data = _config_payload(cfg)
    if is_cli:
        data["mcp_auto_setup"] = auto_configure_mcp_for_provider(
            cfg.provider,
            cli_command=getattr(cfg, "cli_command", None),
            permission_pack="auto",
        )
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
    configs = db.query(APIConfig).order_by(APIConfig.created_at.desc()).all()
    items = [_config_payload(cfg) for cfg in configs]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/config/models")
def create_or_update_model_config(payload: APIConfigCreate, db: Session = Depends(get_db)):
    """Add or update an API or local CLI config."""

    provider_type = _normalize_provider_type(payload.provider, payload.provider_type)
    is_cli = provider_type == LOCAL_CLI_PROVIDER_TYPE or is_local_cli_provider(payload.provider)
    is_runtime = provider_type == LOCAL_RUNTIME_PROVIDER_TYPE or payload.provider == "local_llama_cpp"

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
        if not payload.api_key:
            raise ValidationError("API Key is required for API providers")
        _resolve_base_url(payload.provider, payload.base_url_override)
        api_key = payload.api_key
        base_url_override = payload.base_url_override
        cli_command = None
        cli_args = None

    default_model = _normalize_model_for_provider(payload.provider, payload.default_model)
    existing = db.query(APIConfig).filter(APIConfig.provider == payload.provider).first()
    encrypted_key = encrypt(api_key)

    if existing:
        existing.api_key_encrypted = encrypted_key
        existing.default_model = default_model
        existing.provider_type = LOCAL_CLI_PROVIDER_TYPE if is_cli else LOCAL_RUNTIME_PROVIDER_TYPE if is_runtime else "api"
        existing.base_url_override = base_url_override
        existing.cli_command = cli_command
        existing.cli_args = cli_args
        existing.max_output_tokens = payload.max_output_tokens
        existing.deconstruct_input_char_limit = payload.deconstruct_input_char_limit
        existing.deconstruct_item_char_limit = payload.deconstruct_item_char_limit
        db.commit()
        db.refresh(existing)
        return ApiResponse.success(
            data=_config_payload_with_mcp_setup(existing, is_cli=is_cli),
            message=f"{payload.provider} 配置已更新",
        )

    config = APIConfig(
        provider=payload.provider,
        api_key_encrypted=encrypted_key,
        default_model=default_model,
        provider_type=LOCAL_CLI_PROVIDER_TYPE if is_cli else LOCAL_RUNTIME_PROVIDER_TYPE if is_runtime else "api",
        base_url_override=base_url_override,
        cli_command=cli_command,
        cli_args=cli_args,
        max_output_tokens=payload.max_output_tokens,
        deconstruct_input_char_limit=payload.deconstruct_input_char_limit,
        deconstruct_item_char_limit=payload.deconstruct_item_char_limit,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return ApiResponse.success(
        data=_config_payload_with_mcp_setup(config, is_cli=is_cli),
        message=f"{payload.provider} 配置已添加",
    )


@router.get("/config/models/{provider}")
def get_model_config_detail(provider: str, db: Session = Depends(get_db)):
    config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")
    return ApiResponse.success(data=_config_payload(config, include_masked_key=True))


async def _list_openai_compatible_models(api_key: str, base_url: str, provider: str) -> list[dict]:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    try:
        result = await asyncio.wait_for(client.models.list(), timeout=20)
        seen: set[str] = set()
        models: list[dict] = []
        for m in result.data:
            if m.id not in seen:
                seen.add(m.id)
                models.append({"id": m.id, "display_name": m.id})
        models.sort(key=lambda x: x["id"])
        return models[:100]
    except OpenAIAuthError:
        raise LLMError(f"{_provider_label(provider)} API key is invalid")
    except OpenAIConnectionError:
        raise LLMError(f"Cannot connect to {_provider_label(provider)}")
    except OpenAIAPIError as exc:
        raise LLMError(f"{_provider_label(provider)} API error: {exc}")
    except asyncio.TimeoutError:
        raise LLMError("Request timed out")


async def _list_anthropic_models(api_key: str, base_url: str) -> list[dict]:
    url = f"{base_url}/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers=headers)
        if resp.status_code == 401:
            raise LLMError("Anthropic API key is invalid")
        resp.raise_for_status()
        data = resp.json()
        models = [
            {"id": m["id"], "display_name": m.get("display_name", m["id"])}
            for m in data.get("data", [])
        ]
        models.sort(key=lambda x: x["id"])
        return models[:100]
    except httpx.ConnectError:
        raise LLMError("Cannot connect to Anthropic")
    except httpx.TimeoutException:
        raise LLMError("Request timed out")
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"Anthropic API error: HTTP {exc.response.status_code}")
    except LLMError:
        raise


@router.post("/config/models/list")
async def list_provider_models(payload: ModelListRequest):
    if is_local_cli_provider(payload.provider):
        models = local_cli_model_options(payload.provider, payload.cli_command, payload.cli_args)
    elif payload.provider == "local_llama_cpp":
        models = []
    else:
        if not payload.api_key:
            raise ValidationError("API Key is required")
        base_url = _resolve_base_url(payload.provider, payload.base_url_override)
        if _is_anthropic_provider(payload.provider):
            models = await _list_anthropic_models(payload.api_key, base_url)
        else:
            models = await _list_openai_compatible_models(payload.api_key, base_url, payload.provider)

    models = _normalize_model_list_for_provider(payload.provider, models)
    return ApiResponse.success(data={"models": models}, message=f"Fetched {len(models)} models")


@router.post("/config/models/test")
async def test_connection(payload: ConnectionTestRequest):
    if is_local_cli_provider(payload.provider):
        command = payload.cli_command or _default_cli_command(payload.provider)
        _validate_cli_command(command)
        adapter = LocalCLIAdapter(
            api_key="",
            base_url=payload.provider,
            cli_command=command,
            cli_args=payload.cli_args or _default_cli_args(payload.provider),
        )
        model = payload.model or DEFAULT_CLI_MODELS.get(payload.provider, f"{payload.provider}-default")
        expected_token = "连接成功"
        try:
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
                        "local_cli_cwd": str(resolve_content_root()),
                        "local_cli_timeout_seconds": DEFAULT_LOCAL_CLI_TIMEOUT,
                    },
                ),
                timeout=DEFAULT_LOCAL_CLI_TIMEOUT + LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise LLMError(
                f"{_provider_label(payload.provider)} 在 {DEFAULT_LOCAL_CLI_TIMEOUT} 秒内未响应"
            ) from exc
        reply = (result.get("content") or "").strip()
        if not reply:
            raise LLMError(f"{_provider_label(payload.provider)} returned an empty response")
        if expected_token not in reply:
            raise LLMError(
                f"{_provider_label(payload.provider)} returned an unexpected test reply: {reply[:200]}"
            )
        return ApiResponse.success(
            data={"model": model, "reply": reply[:200]},
            message=f"{_provider_label(payload.provider)} real conversation succeeded",
        )
    if payload.provider == "local_llama_cpp":
        model = payload.model
        if not model:
            raise ValidationError("请选择已安装的本地模型")
        result = await LLMGateway.chat_completion(
            messages=[{"role": "user", "content": "只回复：连接成功"}],
            model=f"local_llama_cpp:{model}",
            temperature=0,
            max_tokens=32,
            retry=0,
            timeout=180,
        )
        return ApiResponse.success(
            data={"model": model, "reply": (result.get("content") or "")[:200]},
            message="本地模型连接成功",
        )

    if not payload.api_key:
        raise ValidationError("API Key is required")
    base_url = _resolve_base_url(payload.provider, payload.base_url_override)

    try:
        if _is_anthropic_provider(payload.provider):
            url = f"{base_url}/v1/models"
            headers = {"x-api-key": payload.api_key, "anthropic-version": "2023-06-01"}
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url, headers=headers)
            if resp.status_code == 401:
                raise LLMError("Anthropic API key is invalid")
            resp.raise_for_status()
        else:
            client = AsyncOpenAI(api_key=payload.api_key, base_url=base_url)
            await asyncio.wait_for(client.models.list(), timeout=15)
        return ApiResponse.success(message=f"{_provider_label(payload.provider)} connection succeeded")
    except OpenAIAuthError:
        raise LLMError(f"{_provider_label(payload.provider)} API key is invalid")
    except OpenAIConnectionError:
        raise LLMError(f"Cannot connect to {_provider_label(payload.provider)}")
    except OpenAIAPIError as exc:
        raise LLMError(f"{_provider_label(payload.provider)} API error: {exc}")
    except httpx.ConnectError:
        raise LLMError("Cannot connect to Anthropic")
    except httpx.TimeoutException:
        raise LLMError("Request timed out")
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"Anthropic API error: HTTP {exc.response.status_code}")
    except asyncio.TimeoutError:
        raise LLMError("Request timed out")


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
    config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")
    db.delete(config)
    db.commit()
    return ApiResponse.success(message=f"{provider} 配置已删除")


@router.get("/config/global-model")
def get_global_model(db: Session = Depends(get_db)):
    config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()  # noqa: E712
    if not config:
        return ApiResponse.success(data={"provider": None, "model": None}, message="未设置全局默认模型")
    return ApiResponse.success(data={
        "provider": config.provider,
        "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False),
    })


@router.put("/config/global-model")
def set_global_model(payload: GlobalModelSetting, db: Session = Depends(get_db)):
    config = db.query(APIConfig).filter(APIConfig.provider == payload.provider).first()
    if not config:
        raise NotFoundError(f"未找到提供商 '{payload.provider}' 的配置，请先添加API配置")

    db.query(APIConfig).update({"is_global_default": False})
    config.is_global_default = True
    if payload.model:
        config.default_model = _normalize_model_for_provider(payload.provider, payload.model)
    db.commit()
    db.refresh(config)
    return ApiResponse.success(
        data={
            "provider": config.provider,
            "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False),
        },
        message=f"全局默认模型已设置为 {config.provider}:{config.default_model}",
    )
