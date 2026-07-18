"""
Test cases for 【大模型API配置与LLM网关】功能子需求 (FR-014).

Covers:
  API Config CRUD:
    - GET    /api/v1/config/models              — list all providers
    - POST   /api/v1/config/models              — create/update API config
    - GET    /api/v1/config/models/{provider}   — get provider detail (masked key)
    - DELETE /api/v1/config/models/{provider}   — delete provider config

  Global Default Model:
    - GET    /api/v1/config/global-model        — get current global default
    - PUT    /api/v1/config/global-model        — set global default

  LLM Gateway:
    - POST   /api/v1/chat/completion            — non-streaming chat
    - POST   /api/v1/chat/completion/stream     — streaming chat (SSE)

  Adapters:
    - OpenAI, Anthropic Claude, DeepSeek, 通义千问, Gemini
    - User-defined OpenAI-compatible providers

  Crypto:
    - Encrypt/decrypt roundtrip

Test design principles:
  - Does NOT rely on pre-existing database data.
  - Create-before-use: data needed by a test is created within the test.
  - Cleanup after each test: all created data is removed in setUp/tearDown.
  - LLM Gateway tests use mocking to avoid real API calls.
"""

import os
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

# ---------------------------------------------------------------------------
# MUST set test database BEFORE importing any application modules.
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from fastapi.testclient import TestClient
from app.main import app
from app.database.session import Base, engine, SessionLocal
from app.database.models import APIConfig
from app.core.crypto import encrypt, decrypt
from app.ai.base import BaseAdapter
from app.ai.gateway import LLMGateway, ADAPTER_MAP
from app.ai.openai_adapter import OpenAIAdapter

API_PREFIX = "/api/v1"


async def _collect_async_chunks(generator):
    return [chunk async for chunk in generator]


def _mark_config_ready(provider: str) -> None:
    """Simulate a successful saved-config verification without a real network call."""
    db = SessionLocal()
    try:
        config = db.query(APIConfig).filter(APIConfig.provider == provider).first()
        if config is None:
            raise AssertionError(f"missing test config: {provider}")
        config.readiness_status = "ready"
        config.readiness_json = json.dumps({"source": "test_verification"})
        db.commit()
    finally:
        db.close()


# ===========================================================================
# Part 1: API Config CRUD Tests
# ===========================================================================


class TestAPIConfigListAPI(unittest.TestCase):
    """Test cases for GET /api/v1/config/models (list providers)."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-01: List configs when empty
    # ------------------------------------------------------------------
    def test_list_configs_empty(self):
        """GET /config/models returns empty list when no configs exist."""
        response = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["total"], 0)
        self.assertEqual(body["data"]["items"], [])

    # ------------------------------------------------------------------
    # TC-02: List configs with one item
    # ------------------------------------------------------------------
    def test_list_configs_single(self):
        """GET /config/models returns one config after creation."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openai",
                "api_key": "sk-test-key-12345",
                "default_model": "gpt-4o",
            },
        )

        response = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["data"]["total"], 1)
        self.assertEqual(len(body["data"]["items"]), 1)
        item = body["data"]["items"][0]
        self.assertEqual(item["provider"], "openai")
        self.assertEqual(item["default_model"], "gpt-4o")
        self.assertFalse(item["is_global_default"])
        # API key must NOT be exposed in list
        self.assertNotIn("api_key", item)
        self.assertNotIn("api_key_encrypted", item)
        self.assertNotIn("api_key_masked", item)

    # ------------------------------------------------------------------
    # TC-03: List configs with multiple items
    # ------------------------------------------------------------------
    def test_list_configs_multiple(self):
        """GET /config/models returns all configured providers."""
        providers = [
            {"provider": "openai", "api_key": "sk-openai", "default_model": "gpt-4o"},
            {"provider": "anthropic", "api_key": "sk-claude", "default_model": "claude-3-5-sonnet-20241022"},
            {"provider": "deepseek", "api_key": "sk-deepseek", "default_model": "deepseek-v4-flash"},
        ]
        for cfg in providers:
            self.client.post(f"{API_PREFIX}/config/models", json=cfg)

        response = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["data"]["total"], 3)
        returned_providers = {item["provider"] for item in body["data"]["items"]}
        self.assertEqual(returned_providers, {"openai", "anthropic", "deepseek"})

    # ------------------------------------------------------------------
    # TC-04: List configs ordered by created_at descending
    # ------------------------------------------------------------------
    def test_list_configs_ordered_by_created_at(self):
        """GET /config/models returns configs ordered by created_at descending."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-first", "default_model": "gpt-4o"},
        )
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "deepseek", "api_key": "sk-second", "default_model": "deepseek-v4-flash"},
        )

        response = self.client.get(f"{API_PREFIX}/config/models")
        items = response.json()["data"]["items"]
        # deepseek was created second, should come first (descending)
        self.assertEqual(items[0]["provider"], "deepseek")
        self.assertEqual(items[1]["provider"], "openai")


class TestAPIConfigCreateAPI(unittest.TestCase):
    """Test cases for POST /api/v1/config/models (create/update)."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-05: Create new API config successfully
    # ------------------------------------------------------------------
    def test_create_config_success(self):
        """POST /config/models creates a new provider config."""
        payload = {
            "provider": "openai",
            "api_key": "sk-test-openai-key-abcdef",
            "default_model": "gpt-4o",
            "base_url_override": "https://api.openai.com/v1",
        }
        response = self.client.post(f"{API_PREFIX}/config/models", json=payload)
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("已添加", body["message"])
        data = body["data"]
        self.assertEqual(data["provider"], "openai")
        self.assertEqual(data["default_model"], "gpt-4o")
        self.assertEqual(data["base_url_override"], "https://api.openai.com/v1")
        self.assertIsNotNone(data["id"])
        # API key must not be in response
        self.assertNotIn("api_key", data)

    # ------------------------------------------------------------------
    # TC-06: Create config for all supported providers
    # ------------------------------------------------------------------
    def test_create_all_providers(self):
        """POST /config/models supports all providers."""
        providers = [
            ("openai", "sk-openai-xxx", "gpt-4o"),
            ("anthropic", "sk-anthropic-xxx", "claude-3-5-sonnet-20241022"),
            ("deepseek", "sk-deepseek-xxx", "deepseek-v4-flash"),
            ("qwen", "sk-qwen-xxx", "qwen-max"),
            ("gemini", "sk-gemini-xxx", "gemini-2.5-flash"),
            ("openrouter", "sk-openrouter-xxx", "openai/gpt-4o-mini"),
        ]
        for provider, key, model in providers:
            payload = {"provider": provider, "api_key": key, "default_model": model}
            if provider == "openrouter":
                payload["base_url_override"] = "https://openrouter.example.test/v1"
            response = self.client.post(
                f"{API_PREFIX}/config/models",
                json=payload,
            )
            self.assertEqual(response.status_code, 200,
                             f"Failed to create config for {provider}")
            self.assertEqual(response.json()["data"]["provider"], provider)

        # Verify all providers exist
        list_resp = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(list_resp.json()["data"]["total"], 6)

    # ------------------------------------------------------------------
    # TC-07: Update existing config (re-add same provider)
    # ------------------------------------------------------------------
    def test_update_existing_config(self):
        """POST /config/models with same provider updates existing config."""
        # Create first
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-old-key", "default_model": "gpt-3.5-turbo"},
        )

        # Update with new key and model
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-new-key", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("已更新", body["message"])
        self.assertEqual(body["data"]["provider"], "openai")
        self.assertEqual(body["data"]["default_model"], "gpt-4o")

        # Verify only one config exists
        list_resp = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(list_resp.json()["data"]["total"], 1)

    # ------------------------------------------------------------------
    # TC-08: Create custom provider without base URL
    # ------------------------------------------------------------------
    def test_create_custom_provider_requires_base_url(self):
        """POST /config/models requires base_url_override for custom providers."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openrouter", "api_key": "sk-xxx", "default_model": "openai/gpt-4o-mini"},
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], 400)
        self.assertIn("自定义 OpenAI 兼容提供商必须填写自定义 API 端点", body["message"])

    def test_create_custom_openai_compatible_provider(self):
        """POST /config/models accepts custom OpenAI-compatible providers with a base URL."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openrouter",
                "api_key": "sk-openrouter",
                "default_model": "openai/gpt-4o-mini",
                "base_url_override": "https://openrouter.example.test/v1",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["provider"], "openrouter")
        self.assertEqual(data["default_model"], "openai/gpt-4o-mini")
        self.assertEqual(data["base_url_override"], "https://openrouter.example.test/v1")

    def test_create_local_cli_provider_without_api_key(self):
        """POST /config/models accepts local CLI providers without API keys."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "claude_cli",
                "default_model": "claude-code",
                "provider_type": "local_cli",
                "cli_command": "claude",
                "cli_args": '["-p","{prompt}"]',
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["provider"], "claude_cli")
        self.assertEqual(data["provider_type"], "local_cli")
        self.assertEqual(data["default_model"], "claude-code")
        self.assertEqual(data["cli_command"], "claude")
        self.assertEqual(data["cli_args"], '["-p","{prompt}"]')

    def test_create_local_runtime_provider_without_api_key(self):
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "local_llama_cpp",
                "provider_type": "local_runtime",
                "default_model": "qwen3-8b-q4",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("本地 AI 模型暂时已停用", response.json()["message"])

    # ------------------------------------------------------------------
    # TC-09: Create with missing required fields
    # ------------------------------------------------------------------
    def test_create_missing_provider(self):
        """POST /config/models without provider returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"api_key": "sk-xxx", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_missing_api_key(self):
        """POST /config/models without api_key returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_missing_default_model(self):
        """POST /config/models without default_model returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-xxx"},
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # TC-10: Create with empty strings (validation)
    # ------------------------------------------------------------------
    def test_create_empty_provider(self):
        """POST /config/models with empty provider returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "", "api_key": "sk-xxx", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_empty_api_key(self):
        """POST /config/models with empty api_key returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_empty_default_model(self):
        """POST /config/models with empty default_model returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-xxx", "default_model": ""},
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # TC-11: Create with base_url_override as null/None
    # ------------------------------------------------------------------
    def test_create_without_base_url(self):
        """POST /config/models without base_url_override succeeds (optional field)."""
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "qwen", "api_key": "sk-qwen-xxx", "default_model": "qwen-max"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["data"]["base_url_override"])

    # ------------------------------------------------------------------
    # TC-12: Create with very long API key
    # ------------------------------------------------------------------
    def test_create_long_api_key(self):
        """POST /config/models with a long API key succeeds."""
        long_key = "sk-" + "a" * 200
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": long_key, "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 200)


class TestAPIConfigDetailAPI(unittest.TestCase):
    """Test cases for GET /api/v1/config/models/{provider} (detail with masked key)."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-13: Get existing provider detail with masked key
    # ------------------------------------------------------------------
    def test_get_provider_detail_masked_key(self):
        """GET /config/models/{provider} returns detail with masked API key."""
        # Create config first
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-abcdefghijklmnop", "default_model": "gpt-4o"},
        )

        response = self.client.get(f"{API_PREFIX}/config/models/openai")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertEqual(data["provider"], "openai")
        self.assertEqual(data["default_model"], "gpt-4o")
        # Verify masked key format: first 4 + **** + last 4
        self.assertIn("api_key_masked", data)
        self.assertIn("****", data["api_key_masked"])
        # Raw key must not be exposed
        self.assertNotIn("api_key", data)
        self.assertNotIn("api_key_encrypted", data)

    # ------------------------------------------------------------------
    # TC-14: Get non-existent provider returns 404
    # ------------------------------------------------------------------
    def test_get_provider_not_found(self):
        """GET /config/models/{unknown_provider} returns 404."""
        response = self.client.get(f"{API_PREFIX}/config/models/nonexistent")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], 404)

    # ------------------------------------------------------------------
    # TC-15: Masked key for short keys
    # ------------------------------------------------------------------
    def test_get_provider_short_key(self):
        """GET /config/models/{provider} with short key returns '****'."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "short", "default_model": "gpt-4o"},
        )

        response = self.client.get(f"{API_PREFIX}/config/models/openai")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["api_key_masked"], "****")


class TestAPIConfigDeleteAPI(unittest.TestCase):
    """Test cases for DELETE /api/v1/config/models/{provider}."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-16: Delete existing config
    # ------------------------------------------------------------------
    def test_delete_config_success(self):
        """DELETE /config/models/{provider} removes the config."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-to-delete", "default_model": "gpt-4o"},
        )

        response = self.client.delete(f"{API_PREFIX}/config/models/openai")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertIn("已删除", body["message"])

        # Verify it's actually gone
        list_resp = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(list_resp.json()["data"]["total"], 0)

    # ------------------------------------------------------------------
    # TC-17: Delete non-existent config returns 404
    # ------------------------------------------------------------------
    def test_delete_config_not_found(self):
        """DELETE /config/models/{unknown_provider} returns 404."""
        response = self.client.delete(f"{API_PREFIX}/config/models/nonexistent")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 404)

    # ------------------------------------------------------------------
    # TC-18: Delete then recreate same provider
    # ------------------------------------------------------------------
    def test_delete_then_recreate(self):
        """After deleting a config, the same provider can be re-created."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-old", "default_model": "gpt-3.5-turbo"},
        )
        self.client.delete(f"{API_PREFIX}/config/models/openai")

        # Re-create with different model
        response = self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-new", "default_model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["default_model"], "gpt-4o")

        # Verify only one
        list_resp = self.client.get(f"{API_PREFIX}/config/models")
        self.assertEqual(list_resp.json()["data"]["total"], 1)


class TestAPIConfigCryptoVerification(unittest.TestCase):
    """Test cases verifying API key encryption in database."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-19: API key stored encrypted in DB
    # ------------------------------------------------------------------
    def test_api_key_encrypted_in_db(self):
        """API key is stored encrypted, not as plaintext."""
        plaintext_key = "sk-plaintext-secret-key-12345"

        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": plaintext_key, "default_model": "gpt-4o"},
        )

        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.provider == "openai").first()
            self.assertIsNotNone(config)
            # Stored value must not be the plaintext
            self.assertNotEqual(config.api_key_encrypted, plaintext_key)
            # Stored value must be decryptable back to original
            decrypted = decrypt(config.api_key_encrypted)
            self.assertEqual(decrypted, plaintext_key)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-20: Re-encrypt on update
    # ------------------------------------------------------------------
    def test_api_key_re_encrypted_on_update(self):
        """Updating API key produces different encrypted value."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-original", "default_model": "gpt-4o"},
        )

        db = SessionLocal()
        try:
            old_encrypted = db.query(APIConfig).filter(
                APIConfig.provider == "openai"
            ).first().api_key_encrypted
        finally:
            db.close()

        # Update with different key
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-updated", "default_model": "gpt-4o"},
        )

        db = SessionLocal()
        try:
            new_encrypted = db.query(APIConfig).filter(
                APIConfig.provider == "openai"
            ).first().api_key_encrypted
            # Encrypted values should differ
            self.assertNotEqual(old_encrypted, new_encrypted)
            # Decrypted should be the new key
            self.assertEqual(decrypt(new_encrypted), "sk-updated")
        finally:
            db.close()


# ===========================================================================
# Part 2: Global Default Model Tests
# ===========================================================================


class TestGlobalModelAPI(unittest.TestCase):
    """Test cases for GET/PUT /api/v1/config/global-model."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    def _create_config(self, provider: str, is_global_default: bool = False):
        """Helper: create a config and optionally set as global default."""
        default_models = {
            "openai": "openai-model",
            "anthropic": "anthropic-model",
            "deepseek": "deepseek-v4-flash",
            "qwen": "qwen-model",
            "gemini": "gemini-2.5-flash",
        }
        default_model = default_models.get(provider, f"{provider}-model")
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": provider,
                "api_key": f"sk-{provider}-test",
                "default_model": default_model,
            },
        )
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.provider == provider).one()
            config.readiness_status = "ready"
            config.readiness_json = '{"source":"test"}'
            db.commit()
        finally:
            db.close()
        if is_global_default:
            self.client.put(
                f"{API_PREFIX}/config/global-model",
                json={"provider": provider, "model": default_model},
            )

    # ------------------------------------------------------------------
    # TC-21: Get global model when not set
    # ------------------------------------------------------------------
    def test_get_global_model_not_set(self):
        """GET /config/global-model returns null values when no default set."""
        response = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertIsNone(body["data"]["provider"])
        self.assertIsNone(body["data"]["model"])
        self.assertIn("未设置", body["message"])

    # ------------------------------------------------------------------
    # TC-22: Set global model successfully
    # ------------------------------------------------------------------
    def test_set_global_model_success(self):
        """PUT /config/global-model sets the global default provider."""
        self._create_config("openai")

        response = self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["provider"], "openai")
        self.assertEqual(body["data"]["model"], "gpt-4o")
        self.assertIn("全局默认模型已设置", body["message"])

    # ------------------------------------------------------------------
    # TC-23: Set global model for non-existent provider
    # ------------------------------------------------------------------
    def test_set_global_model_provider_not_found(self):
        """PUT /config/global-model with unconfigured provider returns 404."""
        response = self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertIn("请先添加API配置", body["message"])

    # ------------------------------------------------------------------
    # TC-24: Get global model after setting
    # ------------------------------------------------------------------
    def test_get_global_model_after_setting(self):
        """GET /config/global-model returns the configured default after setting."""
        self._create_config("openai", is_global_default=True)

        response = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["data"]["provider"], "openai")
        self.assertEqual(body["data"]["model"], "openai-model")

    # ------------------------------------------------------------------
    # TC-25: Switch global default from one provider to another
    # ------------------------------------------------------------------
    def test_switch_global_default_provider(self):
        """Setting global default on provider B clears provider A's default flag."""
        self._create_config("openai", is_global_default=True)
        self._create_config("deepseek")

        # Verify openai is global default
        resp = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(resp.json()["data"]["provider"], "openai")

        # Switch to deepseek
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "deepseek", "model": "deepseek-v4-flash"},
        )

        # Verify switch
        resp = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(resp.json()["data"]["provider"], "deepseek")
        self.assertEqual(resp.json()["data"]["model"], "deepseek-v4-flash")

        # Verify only one is_global_default in DB
        db = SessionLocal()
        try:
            defaults = db.query(APIConfig).filter(APIConfig.is_global_default == True).count()
            self.assertEqual(defaults, 1)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-26: Only one global default exists at any time
    # ------------------------------------------------------------------
    def test_only_one_global_default(self):
        """Only one provider can be marked as global default."""
        self._create_config("openai", is_global_default=True)
        self._create_config("anthropic")
        self._create_config("deepseek")

        # Set anthropic as global default
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"},
        )

        # Verify exactly one global default
        db = SessionLocal()
        try:
            configs = db.query(APIConfig).all()
            default_count = sum(1 for c in configs if c.is_global_default)
            self.assertEqual(default_count, 1, "Exactly one provider should be global default")
            default_provider = next(c for c in configs if c.is_global_default)
            self.assertEqual(default_provider.provider, "anthropic")
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-27: Setting global default updates model name
    # ------------------------------------------------------------------
    def test_set_global_model_updates_model(self):
        """PUT /config/global-model can update the default model name."""
        self._create_config("openai", is_global_default=True)

        # Change model
        response = self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4-turbo"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["model"], "gpt-4-turbo")

        # Verify in DB
        db = SessionLocal()
        try:
            config = db.query(APIConfig).filter(APIConfig.provider == "openai").first()
            self.assertEqual(config.default_model, "gpt-4-turbo")
        finally:
            db.close()


# ===========================================================================
# Part 3: Crypto Tests
# ===========================================================================


class TestCryptoModule(unittest.TestCase):
    """Test cases for encryption/decryption utilities."""

    # ------------------------------------------------------------------
    # TC-28: Encrypt and decrypt roundtrip
    # ------------------------------------------------------------------
    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt returns original plaintext."""
        plaintext = "sk-my-secret-api-key-12345"
        ciphertext = encrypt(plaintext)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(decrypt(ciphertext), plaintext)

    # ------------------------------------------------------------------
    # TC-29: Encrypt same plaintext produces different ciphertext
    # ------------------------------------------------------------------
    def test_encrypt_non_deterministic(self):
        """Each encryption call produces different output (Fernet uses random IV)."""
        plaintext = "sk-same-key"
        c1 = encrypt(plaintext)
        c2 = encrypt(plaintext)
        # Fernet includes timestamp + random IV, so outputs differ
        self.assertNotEqual(c1, c2)
        # But both decrypt to the same plaintext
        self.assertEqual(decrypt(c1), plaintext)
        self.assertEqual(decrypt(c2), plaintext)

    # ------------------------------------------------------------------
    # TC-30: Encrypt empty string
    # ------------------------------------------------------------------
    def test_encrypt_empty_string(self):
        """Encrypting empty string works."""
        ciphertext = encrypt("")
        self.assertTrue(len(ciphertext) > 0)
        self.assertEqual(decrypt(ciphertext), "")

    # ------------------------------------------------------------------
    # TC-31: Encrypt Unicode characters
    # ------------------------------------------------------------------
    def test_encrypt_unicode(self):
        """Encrypt/decrypt handles Unicode characters."""
        plaintext = "sk-密钥测试-中文-日本語-😀"
        ciphertext = encrypt(plaintext)
        self.assertEqual(decrypt(ciphertext), plaintext)


# ===========================================================================
# Part 4: LLM Gateway Tests (Unit tests with mocking)
# ===========================================================================


class TestLLMGatewayModelParsing(unittest.TestCase):
    """Test LLM Gateway model parsing and routing logic."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-32: Parse model with "provider:model" format
    # ------------------------------------------------------------------
    def test_parse_model_with_provider_prefix(self):
        """_parse_model returns correct (provider, model) for 'provider:model' format."""
        provider, model = LLMGateway._parse_model("openai:gpt-4o")
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o")

    def test_parse_model_anthropic_prefix(self):
        provider, model = LLMGateway._parse_model("anthropic:claude-3-5-sonnet-20241022")
        self.assertEqual(provider, "anthropic")
        self.assertEqual(model, "claude-3-5-sonnet-20241022")

    def test_parse_model_deepseek_prefix(self):
        provider, model = LLMGateway._parse_model("deepseek:deepseek-v4-flash")
        self.assertEqual(provider, "deepseek")
        self.assertEqual(model, "deepseek-v4-flash")

    def test_parse_model_qwen_prefix(self):
        provider, model = LLMGateway._parse_model("qwen:qwen-max")
        self.assertEqual(provider, "qwen")
        self.assertEqual(model, "qwen-max")

    def test_parse_model_gemini_prefix(self):
        provider, model = LLMGateway._parse_model("gemini:gemini-2.5-flash")
        self.assertEqual(provider, "gemini")
        self.assertEqual(model, "gemini-2.5-flash")

    # ------------------------------------------------------------------
    # TC-33: Parse model without provider prefix falls back to resolution
    # ------------------------------------------------------------------
    def test_parse_model_without_prefix_claude(self):
        """Model name containing 'claude' resolves to anthropic provider."""
        # Setup DB config for anthropic
        db = SessionLocal()
        try:
            config = APIConfig(
                provider="anthropic",
                api_key_encrypted="encrypted-key",
                default_model="claude-3-5-sonnet-20241022",
            )
            db.add(config)
            db.commit()
        finally:
            db.close()

        provider, model = LLMGateway._parse_model("claude-3-opus-20240229")
        self.assertEqual(provider, "anthropic")
        self.assertEqual(model, "claude-3-opus-20240229")

    def test_parse_model_without_prefix_deepseek(self):
        """Model name containing 'deepseek' resolves to deepseek provider."""
        provider, model = LLMGateway._parse_model("deepseek-reasoner")
        self.assertEqual(provider, "deepseek")
        self.assertEqual(model, "deepseek-reasoner")

    def test_parse_model_without_prefix_qwen(self):
        """Model name containing 'qwen' resolves to qwen provider."""
        provider, model = LLMGateway._parse_model("qwen-plus")
        self.assertEqual(provider, "qwen")
        self.assertEqual(model, "qwen-plus")

    def test_parse_model_without_prefix_gemini(self):
        """Model name containing 'gemini' resolves to gemini provider."""
        provider, model = LLMGateway._parse_model("gemini-2.5-flash")
        self.assertEqual(provider, "gemini")
        self.assertEqual(model, "gemini-2.5-flash")

    def test_parse_model_without_prefix_defaults_to_openai(self):
        """Unknown model without prefix defaults to openai."""
        provider, model = LLMGateway._parse_model("gpt-4o")
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o")

    # ------------------------------------------------------------------
    # TC-34: Get adapter for valid providers
    # ------------------------------------------------------------------
    def test_get_adapter_openai(self):
        adapter_cls = LLMGateway._get_adapter("openai")
        self.assertEqual(adapter_cls.__name__, "OpenAIAdapter")

    def test_get_adapter_anthropic(self):
        adapter_cls = LLMGateway._get_adapter("anthropic")
        self.assertEqual(adapter_cls.__name__, "AnthropicAdapter")

    def test_get_adapter_deepseek(self):
        adapter_cls = LLMGateway._get_adapter("deepseek")
        self.assertEqual(adapter_cls.__name__, "DeepSeekAdapter")

    def test_get_adapter_qwen(self):
        adapter_cls = LLMGateway._get_adapter("qwen")
        self.assertEqual(adapter_cls.__name__, "QwenAdapter")

    def test_get_adapter_gemini(self):
        adapter_cls = LLMGateway._get_adapter("gemini")
        self.assertEqual(adapter_cls.__name__, "GeminiAdapter")

    # ------------------------------------------------------------------
    # TC-35: Get adapter for user-defined OpenAI-compatible provider
    # ------------------------------------------------------------------
    def test_get_adapter_custom_provider(self):
        """_get_adapter uses OpenAIAdapter for custom OpenAI-compatible providers."""
        adapter_cls = LLMGateway._get_adapter("openrouter")
        self.assertEqual(adapter_cls.__name__, "OpenAIAdapter")

    # ------------------------------------------------------------------
    # TC-36: Load config for non-existent provider
    # ------------------------------------------------------------------
    def test_load_config_not_found(self):
        """_load_config raises NotFoundError for non-existent provider."""
        from app.core.exceptions import NotFoundError
        with self.assertRaises(NotFoundError):
            LLMGateway._load_config("openai")

    # ------------------------------------------------------------------
    # TC-37: ADAPTER_MAP covers all expected providers
    # ------------------------------------------------------------------
    def test_adapter_map_coverage(self):
        """ADAPTER_MAP contains all supported providers."""
        expected = {
            "openai",
            "anthropic",
            "deepseek",
            "qwen",
            "gemini",
            "claude_cli",
            "codex_cli",
            "opencode_cli",
            "mimocode_cli",
            "cursor_cli",
            "kilocode_cli",
            "qwen_code_cli",
            "hermes_cli",
            "openclaw_cli",
            "custom_cli",
            "local_llama_cpp",
        }
        self.assertEqual(set(ADAPTER_MAP.keys()), expected)

    def test_get_adapter_local_cli_provider(self):
        """_get_adapter routes local CLI providers to LocalCLIAdapter."""
        adapter_cls = LLMGateway._get_adapter("claude_cli")
        self.assertEqual(adapter_cls.__name__, "LocalCLIAdapter")

    def test_local_cli_provider_does_not_support_tool_calling(self):
        """Local CLI providers must use text/plan orchestration instead of OpenAI tools."""
        self.assertFalse(LLMGateway.supports_tool_calling("claude_cli:claude-code"))
        self.assertFalse(LLMGateway.supports_tool_calling("codex_cli:codex-cli"))
        self.assertFalse(LLMGateway.supports_tool_calling("opencode_cli:opencode-cli"))
        self.assertFalse(LLMGateway.supports_tool_calling("mimocode_cli:mimocode-cli"))
        self.assertFalse(LLMGateway.supports_tool_calling("cursor_cli:cursor-agent"))
        self.assertFalse(LLMGateway.supports_tool_calling("kilocode_cli:kilocode-cli"))
        self.assertFalse(LLMGateway.supports_tool_calling("qwen_code_cli:qwen-code-cli"))
        self.assertFalse(LLMGateway.supports_tool_calling("hermes_cli:hermes-agent"))
        self.assertFalse(LLMGateway.supports_tool_calling("openclaw_cli:openclaw-agent"))
        self.assertFalse(LLMGateway.supports_tool_calling("local_llama_cpp:qwen3-8b-q4"))


class TestLLMGatewayChatCompletion(unittest.TestCase):
    """Test LLM Gateway chat_completion with mocked adapters."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

        # Setup test config
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openai",
                "api_key": "sk-test-openai-mock-key",
                "default_model": "gpt-4o",
            },
        )
        _mark_config_ready("openai")
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )

    # ------------------------------------------------------------------
    # TC-38: Non-streaming chat completion returns expected structure
    # ------------------------------------------------------------------
    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_chat_completion_returns_expected_structure(self, mock_openai):
        """POST /chat/completion returns correct response structure."""
        # Mock the OpenAI response
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "你好，这是测试回复。"
        mock_completion.choices = [mock_choice]
        mock_completion.model = "gpt-4o"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 100
        mock_usage.total_tokens = 150
        mock_completion.usage = mock_usage
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai.return_value = mock_client

        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "你好"}],
                "model": "openai:gpt-4o",
                "temperature": 0.7,
                "max_tokens": 500,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertIn("content", data)
        self.assertIn("model", data)
        self.assertIn("usage", data)
        self.assertEqual(data["content"], "你好，这是测试回复。")

    # ------------------------------------------------------------------
    # TC-39: Non-streaming chat completion with missing model uses global default
    # ------------------------------------------------------------------
    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_chat_completion_uses_global_default_model(self, mock_openai):
        """POST /chat/completion without model uses global default."""
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "使用默认模型回复。"
        mock_completion.choices = [mock_choice]
        mock_completion.model = "gpt-4o"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30
        mock_completion.usage = mock_usage
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai.return_value = mock_client

        # No model specified — should use global default
        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["model"], "gpt-4o")

    # ------------------------------------------------------------------
    # TC-40: Chat completion with no config raises error
    # ------------------------------------------------------------------
    def test_chat_completion_no_global_default_error(self):
        """POST /chat/completion when no global default is set returns error."""
        # Clear the global default
        db = SessionLocal()
        try:
            db.query(APIConfig).update({"is_global_default": False})
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
            },
        )
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertIn("未配置可用的全局默认模型", body["message"])

    # ------------------------------------------------------------------
    # TC-41: Chat completion with unconfigured custom provider fails
    # ------------------------------------------------------------------
    def test_chat_completion_unconfigured_custom_provider(self):
        """POST /chat/completion with an unconfigured provider returns error."""
        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
                "model": "unknown:some-model",
            },
        )
        self.assertEqual(response.status_code, 502)

    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_chat_completion_custom_openai_compatible_provider(self, mock_openai):
        """Configured custom providers use the OpenAI-compatible adapter and base URL."""
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openrouter",
                "api_key": "sk-openrouter",
                "default_model": "openai/gpt-4o-mini",
                "base_url_override": "https://openrouter.example.test/v1",
            },
        )
        _mark_config_ready("openrouter")

        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "custom provider reply"
        mock_choice.message.tool_calls = None
        mock_completion.choices = [mock_choice]
        mock_completion.model = "openai/gpt-4o-mini"
        mock_completion.usage = None
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai.return_value = mock_client

        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
                "model": "openrouter:openai/gpt-4o-mini",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["content"], "custom provider reply")
        called_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("max_tokens", called_kwargs)
        mock_openai.assert_called_with(
            api_key="sk-openrouter",
            base_url="https://openrouter.example.test/v1",
        )

    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_openai_stream_with_tools_omits_null_max_tokens(self, mock_openai):
        """Streaming tool calls should not send max_tokens when the value is unset."""
        mock_client = MagicMock()

        async def mock_stream_generator():
            chunk = MagicMock()
            chunk.choices = [MagicMock(delta=MagicMock(content=None, tool_calls=None), finish_reason="stop")]
            chunk.usage = None
            yield chunk

        mock_stream = MagicMock()
        mock_stream.__aiter__ = lambda s: mock_stream_generator().__aiter__()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream)
        mock_openai.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        chunks = asyncio.run(_collect_async_chunks(adapter.stream_chat_completion_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
            tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )))

        self.assertEqual(chunks[-1]["type"], "done")
        called_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertTrue(called_kwargs["stream"])
        self.assertIn("tools", called_kwargs)
        self.assertNotIn("max_tokens", called_kwargs)

    # ------------------------------------------------------------------
    # TC-42: Chat completion validation — missing messages
    # ------------------------------------------------------------------
    def test_chat_completion_missing_messages(self):
        """POST /chat/completion without messages returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={},
        )
        self.assertEqual(response.status_code, 422)

    # ------------------------------------------------------------------
    # TC-43: Chat completion validation — temperature out of range
    # ------------------------------------------------------------------
    def test_chat_completion_temperature_out_of_range(self):
        """POST /chat/completion with temperature > 2.0 returns validation error."""
        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
                "temperature": 2.5,
            },
        )
        self.assertEqual(response.status_code, 422)


class TestLLMGatewayStreamCompletion(unittest.TestCase):
    """Test LLM Gateway streaming chat completion endpoint."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

        # Setup test config
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openai",
                "api_key": "sk-test-openai-mock-key",
                "default_model": "gpt-4o",
            },
        )
        _mark_config_ready("openai")
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )

    # ------------------------------------------------------------------
    # TC-44: Stream chat completion returns SSE content type
    # ------------------------------------------------------------------
    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_stream_completion_content_type(self, mock_openai):
        """POST /chat/completion/stream returns text/event-stream content type."""
        mock_client = MagicMock()

        async def mock_stream_generator():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="你好"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="世界"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="！"))]),
            ]
            for chunk in chunks:
                yield chunk

        mock_stream = MagicMock()
        mock_stream.__aiter__ = lambda s: mock_stream_generator().__aiter__()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream)
        mock_openai.return_value = mock_client

        response = self.client.post(
            f"{API_PREFIX}/chat/completion/stream",
            json={
                "messages": [{"role": "user", "content": "你好"}],
                "model": "openai:gpt-4o",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))

    # ------------------------------------------------------------------
    # TC-45: Stream chat completion SSE event format
    # ------------------------------------------------------------------
    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_stream_completion_sse_format(self, mock_openai):
        """POST /chat/completion/stream returns properly formatted SSE events."""
        mock_client = MagicMock()

        async def mock_stream_generator():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="测试"))]),
            ]
            for chunk in chunks:
                yield chunk

        mock_stream = MagicMock()
        mock_stream.__aiter__ = lambda s: mock_stream_generator().__aiter__()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream)
        mock_openai.return_value = mock_client

        response = self.client.post(
            f"{API_PREFIX}/chat/completion/stream",
            json={
                "messages": [{"role": "user", "content": "测试"}],
                "model": "openai:gpt-4o",
            },
        )
        self.assertEqual(response.status_code, 200)

        # Collect SSE events
        content = response.text
        self.assertIn("data:", content)
        # Should have token event
        self.assertIn('"type":"token"', content)
        # Should end with DONE
        self.assertIn("[DONE]", content)


# ===========================================================================
# Part 5: Adapter Tests
# ===========================================================================


class TestAdapterMessageConversion(unittest.TestCase):
    """Test adapter-specific message format conversion logic."""

    # ------------------------------------------------------------------
    # TC-46: Anthropic adapter converts messages correctly
    # ------------------------------------------------------------------
    def test_anthropic_message_conversion_with_system(self):
        """_convert_messages extracts system message from OpenAI format."""
        from app.ai.anthropic_adapter import AnthropicAdapter
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮忙的？"},
        ]
        system, converted = AnthropicAdapter._convert_messages(messages)
        self.assertEqual(system, "你是一个助手")
        self.assertEqual(len(converted), 2)
        self.assertEqual(converted[0], {"role": "user", "content": "你好"})
        self.assertEqual(converted[1], {"role": "assistant", "content": "你好！有什么可以帮忙的？"})

    # ------------------------------------------------------------------
    # TC-47: Anthropic adapter converts messages without system
    # ------------------------------------------------------------------
    def test_anthropic_message_conversion_without_system(self):
        """_convert_messages handles messages without system prompt."""
        from app.ai.anthropic_adapter import AnthropicAdapter
        messages = [
            {"role": "user", "content": "直接提问"},
        ]
        system, converted = AnthropicAdapter._convert_messages(messages)
        self.assertIsNone(system)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0], {"role": "user", "content": "直接提问"})

    # ------------------------------------------------------------------
    # TC-48: Anthropic adapter handles unknown roles as user
    # ------------------------------------------------------------------
    def test_anthropic_message_conversion_unknown_role(self):
        """_convert_messages treats unknown roles as 'user'."""
        from app.ai.anthropic_adapter import AnthropicAdapter
        messages = [
            {"role": "unknown_role", "content": "测试内容"},
        ]
        system, converted = AnthropicAdapter._convert_messages(messages)
        self.assertIsNone(system)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0], {"role": "user", "content": "测试内容"})

    # ------------------------------------------------------------------
    # TC-49: Anthropic adapter handles empty messages list
    # ------------------------------------------------------------------
    def test_anthropic_message_conversion_empty(self):
        """_convert_messages with empty list inserts empty user message."""
        from app.ai.anthropic_adapter import AnthropicAdapter
        system, converted = AnthropicAdapter._convert_messages([])
        self.assertIsNone(system)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0], {"role": "user", "content": ""})

    # ------------------------------------------------------------------
    # TC-50: Adapter provider names
    # ------------------------------------------------------------------
    def test_adapter_provider_names(self):
        """Each adapter returns the correct provider_name."""
        from app.ai.openai_adapter import OpenAIAdapter
        from app.ai.anthropic_adapter import AnthropicAdapter
        from app.ai.deepseek_adapter import DeepSeekAdapter
        from app.ai.qwen_adapter import QwenAdapter
        from app.ai.gemini_adapter import GeminiAdapter

        self.assertEqual(OpenAIAdapter(api_key="sk-test").provider_name, "openai")
        self.assertEqual(AnthropicAdapter(api_key="sk-test").provider_name, "anthropic")
        self.assertEqual(DeepSeekAdapter(api_key="sk-test").provider_name, "deepseek")
        self.assertEqual(QwenAdapter(api_key="sk-test").provider_name, "qwen")
        self.assertEqual(GeminiAdapter(api_key="sk-test").provider_name, "gemini")

    # ------------------------------------------------------------------
    # TC-51: DeepSeek adapter uses default base_url
    # ------------------------------------------------------------------
    def test_deepseek_adapter_default_base_url(self):
        """DeepSeek adapter has correct DEFAULT_BASE_URL."""
        from app.ai.deepseek_adapter import DeepSeekAdapter
        self.assertEqual(
            DeepSeekAdapter.DEFAULT_BASE_URL,
            "https://api.deepseek.com",
        )

    # ------------------------------------------------------------------
    # TC-52: Qwen adapter uses default base_url
    # ------------------------------------------------------------------
    def test_qwen_adapter_default_base_url(self):
        """Qwen adapter has correct DEFAULT_BASE_URL."""
        from app.ai.qwen_adapter import QwenAdapter
        self.assertEqual(
            QwenAdapter.DEFAULT_BASE_URL,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_gemini_adapter_default_base_url(self):
        """Gemini adapter has correct DEFAULT_BASE_URL."""
        from app.ai.gemini_adapter import GeminiAdapter
        self.assertEqual(
            GeminiAdapter.DEFAULT_BASE_URL,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    # ------------------------------------------------------------------
    # TC-53: DeepSeek adapter uses provided base_url over default
    # ------------------------------------------------------------------
    def test_deepseek_adapter_custom_base_url(self):
        """DeepSeek adapter uses provided base_url when given."""
        from app.ai.deepseek_adapter import DeepSeekAdapter
        adapter = DeepSeekAdapter(api_key="sk-test", base_url="https://custom.api.com")
        client = adapter._get_client()
        self.assertEqual(client.base_url, "https://custom.api.com")

    # ------------------------------------------------------------------
    # TC-54: OpenAI adapter with custom base_url
    # ------------------------------------------------------------------
    def test_openai_adapter_custom_base_url(self):
        """OpenAI adapter uses provided base_url when given."""
        from app.ai.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter(api_key="sk-test", base_url="https://proxy.example.com/v1")
        client = adapter._get_client()
        self.assertEqual(client.base_url, "https://proxy.example.com/v1")


# ===========================================================================
# Part 6: Integration — Config → LLM Flow
# ===========================================================================


class TestConfigLLMIntegration(unittest.TestCase):
    """Integration tests verifying config and LLM gateway work together."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("test_novel_agent.db")
        except OSError:
            pass

    def setUp(self):
        db = SessionLocal()
        try:
            db.query(APIConfig).delete()
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # TC-55: Configure then call LLM Gateway (mocked)
    # ------------------------------------------------------------------
    @patch("app.ai.openai_adapter.AsyncOpenAI")
    def test_full_config_to_chat_flow(self, mock_openai):
        """Full flow: create config → set global default → make chat call."""
        # Step 1: Create API config
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "deepseek",
                "api_key": "sk-deepseek-test-key",
                "default_model": "deepseek-v4-flash",
            },
        )
        _mark_config_ready("deepseek")

        # Step 2: Set as global default
        resp = self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "deepseek", "model": "deepseek-v4-flash"},
        )
        self.assertEqual(resp.status_code, 200)

        # Step 3: Verify global default
        resp = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(resp.json()["data"]["provider"], "deepseek")

        # Step 4: Make a chat call using deepseek (mocked)
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "DeepSeek 回复"
        mock_completion.choices = [mock_choice]
        mock_completion.model = "deepseek-v4-flash"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30
        mock_completion.usage = mock_usage
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai.return_value = mock_client

        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "你好"}],
                "model": "deepseek:deepseek-v4-flash",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["content"], "DeepSeek 回复")

    # ------------------------------------------------------------------
    # TC-56: Delete config and verify LLM Gateway fails
    # ------------------------------------------------------------------
    def test_delete_config_then_chat_fails(self):
        """After deleting the config, LLM Gateway call should fail."""
        # Create and set global default
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={
                "provider": "openai",
                "api_key": "sk-openai-key",
                "default_model": "gpt-4o",
            },
        )
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )

        # Delete the config
        self.client.delete(f"{API_PREFIX}/config/models/openai")

        # Try chat call — should fail
        response = self.client.post(
            f"{API_PREFIX}/chat/completion",
            json={
                "messages": [{"role": "user", "content": "测试"}],
                "model": "openai:gpt-4o",
            },
        )
        self.assertNotEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # TC-57: Configure multiple providers, switch global default
    # ------------------------------------------------------------------
    def test_switch_provider_and_chat(self):
        """Switch global default between configured providers works correctly."""
        # Setup two providers
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "openai", "api_key": "sk-openai", "default_model": "gpt-4o"},
        )
        self.client.post(
            f"{API_PREFIX}/config/models",
            json={"provider": "deepseek", "api_key": "sk-deepseek", "default_model": "deepseek-v4-flash"},
        )
        _mark_config_ready("openai")
        _mark_config_ready("deepseek")

        # Set openai as default
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "openai", "model": "gpt-4o"},
        )

        # Verify openai is default
        resp = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(resp.json()["data"]["provider"], "openai")

        # Switch to deepseek
        self.client.put(
            f"{API_PREFIX}/config/global-model",
            json={"provider": "deepseek", "model": "deepseek-v4-flash"},
        )

        # Verify deepseek is now default
        resp = self.client.get(f"{API_PREFIX}/config/global-model")
        self.assertEqual(resp.json()["data"]["provider"], "deepseek")

        # Verify openai is no longer default
        db = SessionLocal()
        try:
            openai_cfg = db.query(APIConfig).filter(APIConfig.provider == "openai").first()
            self.assertFalse(openai_cfg.is_global_default)
            deepseek_cfg = db.query(APIConfig).filter(APIConfig.provider == "deepseek").first()
            self.assertTrue(deepseek_cfg.is_global_default)
        finally:
            db.close()


class TestLocalCLIConnectionTimeout(unittest.TestCase):
    def test_unexpected_local_cli_reply_is_not_reported_as_success(self):
        from app.core.exceptions import LLMError
        from app.routers.config import test_connection
        from app.schemas.config import ConnectionTestRequest

        payload = ConnectionTestRequest(
            provider="codex_cli",
            cli_command="codex",
            cli_args='["exec", "{prompt}"]',
            model="codex-cli",
        )
        with patch("app.routers.config._validate_cli_command"), patch(
            "app.modules.model_runtime.infrastructure.verification.LocalCLIAdapter"
        ), patch(
            "app.modules.model_runtime.infrastructure.verification.asyncio.wait_for",
            new=AsyncMock(return_value={"content": "I read the file, but it contained question marks."}),
        ):
            with self.assertRaises(LLMError) as caught:
                asyncio.run(test_connection(payload))

        self.assertIn("unexpected test reply", caught.exception.message)

    def test_timeout_returns_provider_specific_llm_error(self):
        from app.core.exceptions import LLMError
        from app.routers.config import test_connection
        from app.schemas.config import ConnectionTestRequest

        payload = ConnectionTestRequest(
            provider="codex_cli",
            cli_command="codex",
            cli_args='["exec", "{prompt}"]',
            model="codex-cli",
        )
        with patch("app.routers.config._validate_cli_command"), patch(
            "app.modules.model_runtime.infrastructure.verification.LocalCLIAdapter"
        ) as adapter_cls, patch(
            "app.modules.model_runtime.infrastructure.verification.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            adapter_cls.return_value.chat_completion = MagicMock(return_value=None)
            with self.assertRaises(LLMError) as caught:
                asyncio.run(test_connection(payload))

        self.assertIn("Codex CLI", caught.exception.message)
        self.assertIn("180", caught.exception.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
