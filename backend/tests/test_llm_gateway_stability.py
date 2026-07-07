"""Regression tests for LLM gateway request hardening."""

import asyncio
import os
import unittest
from typing import Optional

os.environ["DATABASE_URL"] = "sqlite:///./test_novel_agent.db"

from app.ai.base import BaseAdapter
from app.ai.gateway import ADAPTER_MAP, LLMGateway
from app.core.crypto import encrypt
from app.core.exceptions import LLMError
from app.database.models import APIConfig
from app.database.session import Base, SessionLocal, engine


class FakeAdapter(BaseAdapter):
    last_tool_choice = object()
    calls = 0
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
        yield "ok"

    async def stream_chat_completion_with_tools(self, *args, **kwargs):
        yield {"type": "done", "finish_reason": "stop", "usage": None}


class GatewayStabilityTestCase(unittest.TestCase):
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
            db.add(APIConfig(
                provider="gemini",
                api_key_encrypted=encrypt("test-key"),
                default_model="gemini-2.5-flash",
                is_global_default=True,
            ))
            db.commit()
        finally:
            db.close()
        FakeAdapter.calls = 0
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


if __name__ == "__main__":
    unittest.main()
