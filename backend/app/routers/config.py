"""API config CRUD, global default model, model listing, and connection test endpoints."""
import asyncio
import json
import webbrowser
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from openai import (
    AsyncOpenAI,
    APIError as OpenAIAPIError,
    AuthenticationError as OpenAIAuthError,
    APIConnectionError as OpenAIConnectionError,
)
from sqlalchemy.orm import Session

import httpx
from pydantic import BaseModel, Field

from ..ai.gateway import LLMGateway
from ..database.session import get_db
from ..database.models import APIConfig
from ..schemas.config import (
    APIConfigCreate, GlobalModelSetting,
    ModelListRequest, ConnectionTestRequest,
)
from ..core.response import ApiResponse
from ..core.exceptions import AppException, NotFoundError, ValidationError, LLMError
from ..core.crypto import encrypt, decrypt
from ..core.model_limits import limits_payload

router = APIRouter(tags=["config"])


class ChatCompletionRequest(BaseModel):
    """OpenAI-style chat completion request for compatibility/testing."""
    messages: list[dict] = Field(..., min_length=1)
    model: str | None = None
    temperature: float = Field(0.7, ge=0, le=2.0)
    max_tokens: int | None = Field(None, ge=1)
    extra_body: dict | None = None


@router.post("/system/open-home")
def open_home_in_default_browser(request: Request):
    """Open the Moshu web home in the user's default browser."""
    home_url = str(request.base_url).rstrip("/") + "/"
    webbrowser.open(home_url)
    return ApiResponse.success(data={"url": home_url}, message="已在默认浏览器打开墨枢首页")

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
    "qwen": "通义千问",
    "gemini": "Google Gemini",
}

DEEPSEEK_SUPPORTED_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
DEEPSEEK_MODEL_ALIASES = {"deepseek-v3": "deepseek-v4-flash"}


def _mask_key(key: str) -> str:
    """Mask API key for display: show first 4 and last 4 chars only."""
    if len(key) <= 12:
        return "****"
    return key[:4] + "****" + key[-4:]


def _resolve_base_url(provider: str, base_url_override: str | None) -> str:
    """Return the effective base URL for a provider."""
    if base_url_override:
        return base_url_override.rstrip("/")
    if provider not in PROVIDER_DEFAULT_BASE_URLS:
        raise ValidationError("自定义 OpenAI 兼容提供商必须填写自定义 API 端点")
    return PROVIDER_DEFAULT_BASE_URLS[provider]


def _provider_label(provider: str) -> str:
    """Return a user-facing provider label."""
    return PROVIDER_LABELS.get(provider, provider)


def _is_anthropic_provider(provider: str) -> bool:
    """Anthropic is the only non-OpenAI-compatible provider family today."""
    return provider == "anthropic"


def _normalize_model_for_provider(provider: str, model: str, *, strict: bool = True) -> str:
    """Normalize provider-specific legacy model names and reject unsupported known models."""
    if provider == "gemini":
        return model.removeprefix("models/")
    if provider != "deepseek":
        return model
    normalized = DEEPSEEK_MODEL_ALIASES.get(model, model)
    if normalized not in DEEPSEEK_SUPPORTED_MODELS and strict:
        supported = "、".join(sorted(DEEPSEEK_SUPPORTED_MODELS))
        raise ValidationError(f"DeepSeek 当前支持的模型为 {supported}，请重新选择")
    return normalized


def _normalize_model_list_for_provider(provider: str, models: list[dict]) -> list[dict]:
    """Return model options that are valid for the provider's current API contract."""
    if provider == "gemini":
        normalized: dict[str, dict] = {}
        for model in models:
            model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
            if model_id:
                normalized[model_id] = {"id": model_id, "display_name": model_id}
        return list(normalized.values())
    if provider != "deepseek":
        return models
    normalized: dict[str, dict] = {}
    for model in models:
        model_id = _normalize_model_for_provider(provider, model.get("id", ""), strict=False)
        if model_id in DEEPSEEK_SUPPORTED_MODELS:
            normalized[model_id] = {"id": model_id, "display_name": model_id}
    return list(normalized.values()) or [
        {"id": model_id, "display_name": model_id}
        for model_id in sorted(DEEPSEEK_SUPPORTED_MODELS)
    ]


def _config_payload(cfg: APIConfig, include_masked_key: bool = False) -> dict:
    data = {
        "id": cfg.id,
        "provider": cfg.provider,
        "default_model": _normalize_model_for_provider(cfg.provider, cfg.default_model, strict=False),
        "is_global_default": cfg.is_global_default,
        "base_url_override": cfg.base_url_override,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }
    data.update(limits_payload(
        cfg.provider,
        data["default_model"],
        max_output_tokens=cfg.max_output_tokens,
        deconstruct_input_char_limit=cfg.deconstruct_input_char_limit,
        deconstruct_item_char_limit=cfg.deconstruct_item_char_limit,
    ))
    if include_masked_key:
        masked_key = "未配置"
        try:
            decrypted = decrypt(cfg.api_key_encrypted)
            masked_key = _mask_key(decrypted)
        except Exception:
            pass
        data["api_key_masked"] = masked_key
    return data


@router.get("/config/models")
def list_model_configs(db: Session = Depends(get_db)):
    """Get all configured model providers (without API keys)."""
    configs = db.query(APIConfig).order_by(APIConfig.created_at.desc()).all()
    items = []
    for cfg in configs:
        items.append(_config_payload(cfg))
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/config/models")
def create_or_update_model_config(payload: APIConfigCreate, db: Session = Depends(get_db)):
    """Add or update an API config (encrypts API Key before storage)."""
    # Known providers can use built-in endpoints. Unknown providers are treated
    # as OpenAI-compatible and must provide a base URL.
    _resolve_base_url(payload.provider, payload.base_url_override)

    default_model = _normalize_model_for_provider(payload.provider, payload.default_model)
    existing = db.query(APIConfig).filter(APIConfig.provider == payload.provider).first()

    encrypted_key = encrypt(payload.api_key)

    if existing:
        existing.api_key_encrypted = encrypted_key
        existing.default_model = default_model
        if payload.base_url_override is not None:
            existing.base_url_override = payload.base_url_override or None
        existing.max_output_tokens = payload.max_output_tokens
        existing.deconstruct_input_char_limit = payload.deconstruct_input_char_limit
        existing.deconstruct_item_char_limit = payload.deconstruct_item_char_limit
        db.commit()
        db.refresh(existing)
        return ApiResponse.success(
            data=_config_payload(existing),
            message=f"{payload.provider} 配置已更新",
        )

    config = APIConfig(
        provider=payload.provider,
        api_key_encrypted=encrypted_key,
        default_model=default_model,
        base_url_override=payload.base_url_override,
        max_output_tokens=payload.max_output_tokens,
        deconstruct_input_char_limit=payload.deconstruct_input_char_limit,
        deconstruct_item_char_limit=payload.deconstruct_item_char_limit,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return ApiResponse.success(
        data=_config_payload(config),
        message=f"{payload.provider} 配置已添加",
    )


@router.get("/config/models/{provider}")
def get_model_config_detail(provider: str, db: Session = Depends(get_db)):
    """Get one provider config with a masked API key for legacy callers."""
    config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
    if not config:
        raise NotFoundError(f"Provider config '{provider}' not found")
    return ApiResponse.success(data=_config_payload(config, include_masked_key=True))


async def _list_openai_compatible_models(api_key: str, base_url: str, provider: str) -> list[dict]:
    """Fetch model list from an OpenAI-compatible API."""
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
        raise LLMError(f"{_provider_label(provider)} API Key 无效，请检查后重试")
    except OpenAIConnectionError:
        raise LLMError(f"无法连接到 {_provider_label(provider)} 服务器，请检查网络或自定义端点地址")
    except OpenAIAPIError as e:
        raise LLMError(f"{_provider_label(provider)} API 返回错误: {e}")
    except asyncio.TimeoutError:
        raise LLMError("请求超时，请稍后重试")


async def _list_anthropic_models(api_key: str, base_url: str) -> list[dict]:
    """Fetch model list from Anthropic API."""
    url = f"{base_url}/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers=headers)
        if resp.status_code == 401:
            raise LLMError("Anthropic API Key 无效，请检查后重试")
        resp.raise_for_status()
        data = resp.json()
        models = [
            {"id": m["id"], "display_name": m.get("display_name", m["id"])}
            for m in data.get("data", [])
        ]
        models.sort(key=lambda x: x["id"])
        return models[:100]
    except httpx.ConnectError:
        raise LLMError("无法连接到 Anthropic 服务器，请检查网络或自定义端点地址")
    except httpx.TimeoutException:
        raise LLMError("请求超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise LLMError(f"Anthropic API 返回错误: HTTP {e.response.status_code}")
    except LLMError:
        raise


@router.post("/config/models/list")
async def list_provider_models(payload: ModelListRequest):
    """Fetch available models from a provider using the given API key."""
    base_url = _resolve_base_url(payload.provider, payload.base_url_override)

    if _is_anthropic_provider(payload.provider):
        models = await _list_anthropic_models(payload.api_key, base_url)
    else:
        models = await _list_openai_compatible_models(payload.api_key, base_url, payload.provider)

    models = _normalize_model_list_for_provider(payload.provider, models)
    return ApiResponse.success(data={"models": models}, message=f"获取到 {len(models)} 个模型")


@router.post("/config/models/test")
async def test_connection(payload: ConnectionTestRequest):
    """Test API connection with the given credentials."""
    base_url = _resolve_base_url(payload.provider, payload.base_url_override)

    try:
        if _is_anthropic_provider(payload.provider):
            url = f"{base_url}/v1/models"
            headers = {"x-api-key": payload.api_key, "anthropic-version": "2023-06-01"}
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url, headers=headers)
            if resp.status_code == 401:
                raise LLMError("Anthropic API Key 无效，请检查后重试")
            resp.raise_for_status()
        else:
            client = AsyncOpenAI(api_key=payload.api_key, base_url=base_url)
            await asyncio.wait_for(client.models.list(), timeout=15)

        label = _provider_label(payload.provider)
        return ApiResponse.success(message=f"{label} 连接成功，API Key 有效")

    except OpenAIAuthError:
        label = _provider_label(payload.provider)
        raise LLMError(f"{label} API Key 无效，请检查后重试")
    except OpenAIConnectionError:
        label = _provider_label(payload.provider)
        raise LLMError(f"无法连接到 {label} 服务器，请检查网络或自定义端点地址")
    except OpenAIAPIError as e:
        label = _provider_label(payload.provider)
        raise LLMError(f"{label} API 返回错误: {e}")
    except httpx.ConnectError:
        raise LLMError("无法连接到 Anthropic 服务器，请检查网络或自定义端点地址")
    except httpx.TimeoutException:
        raise LLMError("请求超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise LLMError(f"Anthropic API 返回错误: HTTP {e.response.status_code}")
    except asyncio.TimeoutError:
        raise LLMError("请求超时，请稍后重试")


@router.post("/chat/completion")
async def chat_completion(payload: ChatCompletionRequest):
    """Compatibility endpoint for direct non-streaming LLM calls."""
    try:
        result = await LLMGateway.chat_completion(
            messages=payload.messages,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            extra_body=payload.extra_body,
        )
    except AppException as exc:
        # Keep this direct gateway endpoint as an LLM boundary. Older callers
        # expect provider/config resolution failures to be 502-level errors.
        raise LLMError(exc.message)
    return ApiResponse.success(data=result)


@router.post("/chat/completion/stream")
async def chat_completion_stream(payload: ChatCompletionRequest):
    """Compatibility endpoint for direct streaming LLM calls."""

    async def _events():
        try:
            async for chunk in LLMGateway.stream_chat_completion(
                messages=payload.messages,
                model=payload.model,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
                extra_body=payload.extra_body,
            ):
                data = json.dumps(
                    {"type": "token", "content": chunk},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"data: {data}\n\n"
        except AppException as exc:
            data = json.dumps(
                {"type": "error", "message": exc.message},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"data: {data}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.delete("/config/models/{provider}")
def delete_model_config(provider: str, db: Session = Depends(get_db)):
    """Delete a provider's API config."""
    config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
    if not config:
        raise NotFoundError(f"未找到提供商 '{provider}' 的配置")

    db.delete(config)
    db.commit()
    return ApiResponse.success(message=f"{provider} 配置已删除")


@router.get("/config/global-model")
def get_global_model(db: Session = Depends(get_db)):
    """Get the current global default model setting."""
    config = db.query(APIConfig).filter(APIConfig.is_global_default == True).first()
    if not config:
        return ApiResponse.success(
            data={"provider": None, "model": None},
            message="未设置全局默认模型",
        )
    return ApiResponse.success(
        data={"provider": config.provider, "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False)}
    )


@router.put("/config/global-model")
def set_global_model(payload: GlobalModelSetting, db: Session = Depends(get_db)):
    """Set the global default model provider."""
    config = db.query(APIConfig).filter(APIConfig.provider == payload.provider).first()
    if not config:
        raise NotFoundError(f"未找到提供商 '{payload.provider}' 的配置，请先添加API配置")

    # Clear all global defaults first
    db.query(APIConfig).update({"is_global_default": False})

    # Set new global default
    config.is_global_default = True
    if payload.model:
        config.default_model = _normalize_model_for_provider(payload.provider, payload.model)

    db.commit()
    db.refresh(config)
    return ApiResponse.success(
        data={"provider": config.provider, "model": _normalize_model_for_provider(config.provider, config.default_model, strict=False)},
        message=f"全局默认模型已设置为 {config.provider}:{config.default_model}",
    )
