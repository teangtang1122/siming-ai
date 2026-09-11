"""Provider model IDs survive discovery; catalogs only supply capacity facts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.exceptions import LLMError
from app.modules.model_runtime.application.verification import ModelProbeRequest
from app.modules.model_runtime.infrastructure import verification
from app.routers.config import _normalize_model_list_for_provider
from app.schemas.config import (
    APIConfigCreate,
    GlobalModelSetting,
    ProviderModelOption,
    TaskModelSettingUpdate,
)


@pytest.mark.parametrize(("schema", "field"), [
    (APIConfigCreate, "default_model"), (GlobalModelSetting, "model"),
    (TaskModelSettingUpdate, "model"), (ProviderModelOption, "id"),
])
def test_model_ids_keep_structural_validation_without_version_allowlist(schema, field):
    with pytest.raises(ValidationError, match="blank"):
        schema.model_validate({"provider": "deepseek", field: " \t "})
    model = schema.model_validate({"provider": "deepseek", field: " deepseek-future-test-model "})
    assert getattr(model, field) == "deepseek-future-test-model"


@pytest.mark.parametrize("provider", ["openai", "deepseek", "gemini", "qwen", "vendor"])
def test_discovery_keeps_every_provider_model_beyond_first_hundred(monkeypatch, provider):
    ids = [f"{provider}-model-{index:03}" for index in range(105)]
    client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(
            data=[SimpleNamespace(id=model) for model in reversed(ids)],
        ))),
        close=AsyncMock(),
    )
    monkeypatch.setattr(verification, "_openai_client", lambda **kwargs: client)

    models = asyncio.run(verification.ProviderModelVerification().list_models(
        ModelProbeRequest(provider=provider, api_key="test-key", base_url="https://test.invalid/v1"),
    ))

    assert [item["id"] for item in _normalize_model_list_for_provider(provider, models)] == ids
    client.models.list.assert_awaited_once()
    client.close.assert_awaited_once()


@pytest.mark.parametrize("provider", ["deepseek", "gemini", "openai", "anthropic", "qwen"])
def test_empty_provider_list_stays_empty(provider):
    assert _normalize_model_list_for_provider(provider, []) == []


def test_anthropic_reads_remaining_pages_and_preserves_capacity(monkeypatch):
    first_page = [{"id": f"claude-model-{index:03}"} for index in range(100)]
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        if "after_id" not in request.url.params:
            return httpx.Response(200, json={
                "data": first_page, "has_more": True, "last_id": "claude-model-099",
            })
        assert request.url.params["after_id"] == "claude-model-099"
        return httpx.Response(200, json={
            "data": [{"id": "claude-new", "display_name": "New Claude",
                      "max_input_tokens": 200_000, "max_tokens": 32_000}],
            "has_more": False,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(verification.httpx, "AsyncClient", lambda **kwargs: client)
    models = asyncio.run(verification.ProviderModelVerification().list_models(
        ModelProbeRequest(provider="anthropic", api_key="test-key", base_url="https://test.invalid"),
    ))

    assert len(requests) == 2
    assert len(models) == 101
    assert models[-1] == {
        "id": "claude-new", "display_name": "New Claude",
        "context_window_tokens": 200_000, "max_output_tokens": 32_000,
        "safety_margin_tokens": 512, "capacity_source": "anthropic_models_api",
    }


@pytest.mark.parametrize("cursor", [None, "repeated"])
def test_anthropic_invalid_pagination_fails_without_returning_partial_list(monkeypatch, cursor):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "data": [{"id": "claude-test"}], "has_more": True, "last_id": cursor,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(verification.httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(LLMError, match="分页异常"):
        asyncio.run(verification.ProviderModelVerification().list_models(
            ModelProbeRequest(provider="anthropic", api_key="test-key", base_url="https://test.invalid"),
        ))
    assert len(requests) == (1 if cursor is None else 2)
