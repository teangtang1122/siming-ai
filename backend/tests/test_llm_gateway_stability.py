"""Regression tests for LLM gateway request hardening."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from app.ai.base import BaseAdapter
from app.ai.gateway import ADAPTER_MAP, LLMGateway
from app.ai.local_cli_adapter import LOCAL_CLI_TIMEOUT_GRACE_SECONDS, LocalCLIAdapter
from app.core.crypto import encrypt
from app.core.exceptions import LLMError
from app.database.models import APIConfig
from app.database.migrations import ensure_runtime_schema
from app.database.session import Base, SessionLocal, engine
from app.modules.model_runtime.infrastructure.gateway import _apply_active_context_manifest


class FakeAdapter(BaseAdapter):
    last_tool_choice = object()
    calls = 0
    stream_calls = 0
    stream_errors: list[Exception] = []
    error: Exception | None = None

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str | dict] = None,
    ) -> dict:
        FakeAdapter.calls += 1
        if FakeAdapter.error:
            raise FakeAdapter.error
        FakeAdapter.last_tool_choice = tool_choice
        return {"content": "ok", "model": model, "usage": {}, "tool_calls": None}

    async def stream_chat_completion(self, *args, **kwargs):
        FakeAdapter.stream_calls += 1
        if FakeAdapter.stream_errors:
            raise FakeAdapter.stream_errors.pop(0)
        yield "ok"

    async def stream_chat_completion_with_tools(self, *args, **kwargs):
        yield {"type": "done", "finish_reason": "stop", "usage": None}


class GatewayStabilityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)

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
            db.add(APIConfig(
                provider="gemini",
                api_key_encrypted=encrypt("test-key"),
                default_model="gemini-2.5-flash",
                is_global_default=True,
                readiness_status="ready",
                readiness_json='{"source":"test"}',
            ))
            db.commit()
        finally:
            db.close()
        FakeAdapter.calls = 0
        FakeAdapter.stream_calls = 0
        FakeAdapter.stream_errors = []
        FakeAdapter.last_tool_choice = object()
        FakeAdapter.error = None
        self._old_adapter = ADAPTER_MAP.get("gemini")
        ADAPTER_MAP["gemini"] = FakeAdapter

    def tearDown(self):
        if self._old_adapter is not None:
            ADAPTER_MAP["gemini"] = self._old_adapter

    def test_gemini_tool_choice_is_stripped_before_call(self):
        result = asyncio.run(LLMGateway.chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini:gemini-2.5-flash",
            tools=[{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
            tool_choice="auto",
            retry=0,
        ))

        self.assertEqual(result["content"], "ok")
        self.assertIsNone(FakeAdapter.last_tool_choice)
        self.assertEqual(FakeAdapter.calls, 1)
        self.assertIn("tool_choice", result["request_meta"]["adjustments"][0])

    def test_quota_errors_are_not_retried(self):
        FakeAdapter.error = LLMError(
            "本机 CLI 提供方额度/限额已耗尽或触发速率限制："
            "Free usage exceeded, subscribe to Go [retrying in 9h 28m attempt #1]"
        )

        with self.assertRaisesRegex(LLMError, "Free usage exceeded"):
            asyncio.run(LLMGateway.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini:gemini-2.5-flash",
                retry=2,
            ))

        self.assertEqual(FakeAdapter.calls, 1)

    def test_stream_retries_upstream_502_before_first_output(self):
        FakeAdapter.stream_errors = [
            LLMError("OpenAI API 错误: Error code: 502 - upstream request failed"),
            LLMError("OpenAI API 错误: Error code: 502 - upstream request failed"),
        ]

        async def collect():
            return [chunk async for chunk in LLMGateway.stream_chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini:gemini-2.5-flash",
                retry=3,
            )]

        with patch(
            "app.modules.model_runtime.infrastructure.gateway.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = asyncio.run(collect())

        self.assertEqual(result, ["ok"])
        self.assertEqual(FakeAdapter.stream_calls, 3)


    def test_local_cli_timeout_is_owned_by_adapter_before_gateway_wait(self):
        body, wait_timeout = LLMGateway._local_cli_timeout_body(
            LocalCLIAdapter,
            {"local_cli_cwd": r"D:\novels"},
            180,
        )

        self.assertEqual(body["local_cli_cwd"], r"D:\novels")
        self.assertEqual(body["local_cli_timeout_seconds"], 180)
        self.assertEqual(wait_timeout, 180 + LOCAL_CLI_TIMEOUT_GRACE_SECONDS)

    def test_local_cli_can_request_a_short_cleanup_grace_for_interactive_work(self):
        body, wait_timeout = LLMGateway._local_cli_timeout_body(
            LocalCLIAdapter,
            {"local_cli_timeout_grace_seconds": 4},
            45,
        )

        self.assertEqual(body["local_cli_timeout_seconds"], 45)
        self.assertNotIn("local_cli_timeout_grace_seconds", body)
        self.assertEqual(wait_timeout, 49)

    def test_non_local_cli_timeout_body_is_unchanged(self):
        body = {"x": 1}
        updated_body, wait_timeout = LLMGateway._local_cli_timeout_body(FakeAdapter, body, 30)

        self.assertIs(updated_body, body)
        self.assertEqual(wait_timeout, 30)

    def test_governed_context_keeps_a_single_leading_system_message(self):
        active = SimpleNamespace(
            manifest_id="manifest-1",
            output_reserve_tokens=0,
            rendered_context="selected project evidence",
        )
        with patch(
            "app.modules.model_runtime.infrastructure.gateway.active_context_manifest",
            return_value=active,
        ):
            messages, body, _ = _apply_active_context_manifest(
                [
                    {"role": "system", "content": "base instructions"},
                    {"role": "user", "content": "continue"},
                ],
                None,
                None,
            )

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("base instructions", messages[0]["content"])
        self.assertIn("selected project evidence", messages[0]["content"])
        self.assertTrue(body["moshu_context_manifest_rendered"])


if __name__ == "__main__":
    unittest.main()
