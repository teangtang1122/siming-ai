"""Provider SDK and local CLI implementation of model verification."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from openai import (
    APIConnectionError as OpenAIConnectionError,
)
from openai import (
    APIError as OpenAIAPIError,
)
from openai import (
    APITimeoutError as OpenAITimeoutError,
)
from openai import (
    AsyncOpenAI,
)
from openai import (
    AuthenticationError as OpenAIAuthError,
)

from ....ai.anthropic_adapter import AnthropicAdapter
from ....core.exceptions import LLMError, ValidationError
from ..application.verification import ModelProbeRequest
from .gateway import LLMGateway
from .local_cli import (
    DEFAULT_CLI_MODELS,
    DEFAULT_LOCAL_CLI_TIMEOUT,
    LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
    LocalCLIAdapter,
    is_local_cli_provider,
    local_cli_model_options,
)

API_PROTOCOL_AUTO = "auto"
API_PROTOCOL_CHAT = "chat_completions"
API_PROTOCOL_RESPONSES = "responses"


def _provider_label(provider: str) -> str:
    labels = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "deepseek": "DeepSeek",
        "qwen": "Tongyi Qwen",
        "gemini": "Google Gemini",
        "claude_cli": "Claude Code CLI",
        "codex_cli": "Codex CLI",
        "opencode_cli": "OpenCode CLI",
        "mimocode_cli": "MiMo Code CLI",
        "cursor_cli": "Cursor Agent CLI",
        "kilocode_cli": "Kilo Code CLI",
        "qwen_code_cli": "Qwen Code CLI",
        "hermes_cli": "Hermes Agent CLI",
        "openclaw_cli": "OpenClaw CLI",
        "custom_cli": "Custom Local CLI",
    }
    return labels.get(provider, provider)


def _is_anthropic(provider: str) -> bool:
    return provider == "anthropic"


def _is_custom(provider: str) -> bool:
    return provider not in {"openai", "anthropic", "deepseek", "qwen", "gemini"}


def _protocol_label(protocol: str) -> str:
    return "Responses API" if protocol == API_PROTOCOL_RESPONSES else "Chat Completions"


def _protocol_candidates(protocol: str) -> list[str]:
    if protocol == API_PROTOCOL_CHAT:
        return [API_PROTOCOL_CHAT]
    if protocol == API_PROTOCOL_RESPONSES:
        return [API_PROTOCOL_RESPONSES]
    return [API_PROTOCOL_CHAT, API_PROTOCOL_RESPONSES]


def _base_url_candidates(base_url: str, *, allow_v1_fallback: bool) -> list[str]:
    normalized = base_url.rstrip("/")
    candidates = [normalized]
    path = urlsplit(normalized).path.rstrip("/").lower()
    if allow_v1_fallback and not path.endswith(("/v1", "/v1beta/openai")):
        candidates.append(f"{normalized}/v1")
    return candidates


def _error_status(error: BaseException) -> int | None:
    status = getattr(error, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


async def _probe_openai(request: ModelProbeRequest) -> dict:
    attempts: list[str] = []
    for base_url in _base_url_candidates(
        request.base_url,
        allow_v1_fallback=_is_custom(request.provider),
    ):
        for protocol in _protocol_candidates(request.api_protocol):
            client = AsyncOpenAI(api_key=request.api_key, base_url=base_url)
            try:
                if protocol == API_PROTOCOL_RESPONSES:
                    response = await asyncio.wait_for(
                        client.responses.create(
                            model=request.model,
                            input="Reply with exactly: OK",
                            max_output_tokens=128,
                            store=False,
                        ),
                        timeout=30,
                    )
                    reply = str(getattr(response, "output_text", "") or "").strip()
                else:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=request.model,
                            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                            max_tokens=32,
                        ),
                        timeout=30,
                    )
                    reply = str(response.choices[0].message.content or "").strip()
                if not reply:
                    raise LLMError(f"{_protocol_label(protocol)} returned an empty response")
                return {
                    "model": request.model,
                    "reply": reply[:200],
                    "api_protocol": protocol,
                    "base_url": base_url,
                }
            except OpenAIAuthError as exc:
                raise LLMError(f"{_provider_label(request.provider)} API key is invalid") from exc
            except OpenAIConnectionError as exc:
                raise LLMError(f"Cannot connect to {_provider_label(request.provider)}") from exc
            except (TimeoutError, OpenAITimeoutError) as exc:
                raise LLMError(f"{_provider_label(request.provider)} request timed out") from exc
            except OpenAIAPIError as exc:
                status = _error_status(exc)
                if status in {401, 403}:
                    raise LLMError(
                        f"{_provider_label(request.provider)} API key is invalid"
                    ) from exc
                if status == 429:
                    raise LLMError(
                        f"{_provider_label(request.provider)} quota or rate limit reached: {exc}"
                    ) from exc
                prefix = f"HTTP {status}: " if status else ""
                attempts.append(f"{_protocol_label(protocol)} @ {base_url}: {prefix}{exc}"[:600])
            except LLMError as exc:
                attempts.append(f"{_protocol_label(protocol)} @ {base_url}: {exc}")
            finally:
                await client.close()
    detail = "; ".join(attempts[-4:]) or "no compatible endpoint responded"
    raise LLMError(f"{_provider_label(request.provider)} API error: {detail}")


async def _list_openai(request: ModelProbeRequest) -> list[dict]:
    client = AsyncOpenAI(api_key=request.api_key, base_url=request.base_url)
    try:
        result = await asyncio.wait_for(client.models.list(), timeout=20)
        models = sorted({item.id for item in result.data})
        return [{"id": model, "display_name": model} for model in models[:100]]
    except OpenAIAuthError as exc:
        raise LLMError(f"{_provider_label(request.provider)} API key is invalid") from exc
    except OpenAIConnectionError as exc:
        raise LLMError(f"Cannot connect to {_provider_label(request.provider)}") from exc
    except OpenAIAPIError as exc:
        raise LLMError(f"{_provider_label(request.provider)} API error: {exc}") from exc
    except TimeoutError as exc:
        raise LLMError("Request timed out") from exc
    finally:
        await client.close()


async def _list_anthropic(request: ModelProbeRequest) -> list[dict]:
    url = f"{request.base_url}/v1/models"
    headers = {"x-api-key": request.api_key, "anthropic-version": "2023-06-01"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 401:
            raise LLMError("Anthropic API key is invalid")
        response.raise_for_status()
        models = [
            {"id": item["id"], "display_name": item.get("display_name", item["id"])}
            for item in response.json().get("data", [])
        ]
        return sorted(models, key=lambda item: item["id"])[:100]
    except httpx.ConnectError as exc:
        raise LLMError("Cannot connect to Anthropic") from exc
    except httpx.TimeoutException as exc:
        raise LLMError("Request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"Anthropic API error: HTTP {exc.response.status_code}") from exc


class ProviderModelVerification:
    async def list_models(self, request: ModelProbeRequest) -> list[dict]:
        if is_local_cli_provider(request.provider):
            return local_cli_model_options(request.provider, request.cli_command, request.cli_args)
        if request.provider == "local_llama_cpp":
            return []
        if _is_anthropic(request.provider):
            return await _list_anthropic(request)
        return await _list_openai(request)

    async def verify(self, request: ModelProbeRequest) -> dict:
        if is_local_cli_provider(request.provider):
            return await self._verify_cli(request)
        if request.provider == "local_llama_cpp":
            if not request.model:
                raise ValidationError("请选择已安装的本地模型")
            result = await LLMGateway.chat_completion(
                messages=[{"role": "user", "content": "只回复：连接成功"}],
                model=f"local_llama_cpp:{request.model}",
                temperature=0,
                max_tokens=32,
                retry=0,
                timeout=180,
            )
            return {"model": request.model, "reply": str(result.get("content") or "")[:200]}
        if _is_anthropic(request.provider):
            adapter = AnthropicAdapter(api_key=request.api_key, base_url=request.base_url)
            result = await asyncio.wait_for(
                adapter.chat_completion(
                    messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                    model=request.model or "",
                    temperature=0,
                    max_tokens=32,
                ),
                timeout=30,
            )
            reply = str(result.get("content") or "").strip()
            if not reply:
                raise LLMError("Anthropic returned an empty response")
            return {
                "model": request.model,
                "reply": reply[:200],
                "api_protocol": API_PROTOCOL_CHAT,
                "base_url": request.base_url,
            }
        return await _probe_openai(request)

    async def _verify_cli(self, request: ModelProbeRequest) -> dict:
        timeout = request.timeout_seconds or DEFAULT_LOCAL_CLI_TIMEOUT
        adapter = LocalCLIAdapter(
            api_key="",
            base_url=request.provider,
            cli_command=request.cli_command,
            cli_args=request.cli_args,
        )
        model = request.model or DEFAULT_CLI_MODELS.get(
            request.provider,
            f"{request.provider}-default",
        )
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
                        "local_cli_cwd": str(request.content_root or ""),
                        "local_cli_timeout_seconds": timeout,
                    },
                ),
                timeout=timeout + LOCAL_CLI_TIMEOUT_GRACE_SECONDS,
            )
        except TimeoutError as exc:
            raise LLMError(f"{_provider_label(request.provider)} 在 {timeout} 秒内未响应") from exc
        reply = str(result.get("content") or "").strip()
        if not reply:
            raise LLMError(f"{_provider_label(request.provider)} returned an empty response")
        if "连接成功" not in reply:
            raise LLMError(
                f"{_provider_label(request.provider)} returned an unexpected "
                f"test reply: {reply[:200]}"
            )
        return {"model": model, "reply": reply[:200]}


__all__ = ["ProviderModelVerification"]
