"""Tiny OpenAI-compatible streaming server for local E2E smoke tests."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CATALOGING_JSONL = [
    {
        "type": "chapter_summary",
        "payload": {
            "summary_text": "林澈在雾港发现潮汐钟异常，决定调查港口旧塔。",
            "key_events": ["潮汐钟倒转", "林澈进入旧塔"],
            "characters": ["林澈", "守塔老人"],
            "worldbuilding": ["雾港潮汐钟"],
            "outline_hint": "本章建立潮汐钟异常和旧塔调查钩子。",
        },
        "confidence": 0.98,
        "evidence": "潮汐钟倒着走；林澈推门进塔。",
    },
    {
        "type": "character_create",
        "target_name": "林澈",
        "payload": {
            "name": "林澈",
            "role_type": "protagonist",
            "appearance": "原文未明示，按当前表现推定：年轻调查者，常随身携带记录册，衣着适合雾港潮湿环境。",
            "personality": "谨慎、好奇，遇到异常会先记录再行动。",
            "background": "雾港居民或长期调查者，熟悉潮汐钟的正常运行规律。",
            "abilities": ["记录异常现象", "从民俗和机械变化中推断风险"],
            "tone_style": "克制、直接",
            "catchphrases": ["先记下来"],
            "emotion_tendency": "警觉但不慌乱",
            "custom_system_prompt": "你扮演林澈。林澈是雾港旧塔事件的核心调查者，谨慎、好奇，面对异常时会先记录证据，再判断是否行动。说话克制直接，不轻易夸张，也不会无根据地下结论。你知道潮汐钟倒转是不祥征兆，但不知道旧塔深处真正原因。行动时优先保护线索和自身安全，不能主动说出尚未在剧情中确认的真相。",
            "current_location": "雾港旧塔",
            "physical_state": "健康",
            "mental_state": "警觉",
        },
        "confidence": 0.95,
        "evidence": "林澈记录潮汐钟异常并独自进入旧塔。",
    },
    {
        "type": "worldbuilding_create",
        "target_name": "雾港潮汐钟",
        "payload": {
            "dimension": "culture",
            "title": "雾港潮汐钟",
            "content": "雾港居民依靠潮汐钟判断海雾与潮汐，钟面倒转被视为灾兆。",
            "status": "active",
            "confidence": 0.94,
        },
        "confidence": 0.94,
        "evidence": "镇民说潮汐钟倒转是不祥征兆。",
    },
    {
        "type": "outline_create",
        "target_name": "第1章 潮汐钟倒转",
        "payload": {
            "title": "第1章 潮汐钟倒转",
            "node_type": "chapter",
            "summary": "林澈发现雾港潮汐钟倒转并进入旧塔调查。",
            "actual_summary": "林澈发现雾港潮汐钟倒转，镇民称其为不祥征兆，他决定进入港口旧塔调查源头。",
            "related_characters": ["林澈"],
        },
        "confidence": 0.93,
        "evidence": "本章围绕潮汐钟异常和旧塔调查展开。",
    },
    {
        "type": "outline_create",
        "target_name": "第1章-场景1 潮汐钟倒转",
        "payload": {
            "title": "第1章-场景1 潮汐钟倒转",
            "node_type": "section",
            "parent_title": "第1章 潮汐钟倒转",
            "summary": "林澈确认潮汐钟出现反常倒转，雾港居民因此恐慌。",
            "related_characters": ["林澈"],
        },
        "confidence": 0.91,
        "evidence": "潮汐钟倒着走，镇民称其为不祥征兆。",
    },
    {
        "type": "outline_create",
        "target_name": "第1章-场景2 旧塔调查",
        "payload": {
            "title": "第1章-场景2 旧塔调查",
            "node_type": "section",
            "parent_title": "第1章 潮汐钟倒转",
            "summary": "林澈进入港口旧塔，开始寻找潮汐钟倒转的原因。",
            "related_characters": ["林澈"],
        },
        "confidence": 0.9,
        "evidence": "林澈推门进入旧塔。",
    },
    {
        "type": "character_relationship",
        "payload": {
            "source_name": "林澈",
            "target_name": "守塔老人",
            "relationship_type": "线索提供者/调查者",
            "description": "守塔老人向林澈提供潮汐钟倒转的不祥说法，林澈据此展开调查。",
        },
        "confidence": 0.82,
        "evidence": "镇民说潮汐钟倒转是不祥征兆。",
    },
    {
        "type": "chapter_link",
        "payload": {
            "character_names": ["林澈", "守塔老人"],
            "worldbuilding_titles": ["雾港潮汐钟"],
            "outline_title": "第1章 潮汐钟倒转",
        },
        "confidence": 0.92,
        "evidence": "林澈、潮汐钟和旧塔调查均在本章出现。",
    },
]

FACT_JSONL = [
    {
        "fact_type": "chapter_overview",
        "payload": {
            "summary": "林澈发现雾港潮汐钟异常，决定进入旧塔调查。",
            "key_events": ["潮汐钟倒转", "林澈进入旧塔"],
            "scenes": ["雾港街头", "港口旧塔"],
        },
        "confidence": 0.98,
        "evidence": "潮汐钟倒着走；林澈推门进塔。",
    },
    {
        "fact_type": "character_fact",
        "payload": {
            "names": ["林澈"],
            "primary_name": "林澈",
            "role_hint": "调查者",
            "actions": ["记录潮汐钟倒转", "进入旧塔"],
            "location": "雾港旧塔",
            "mental_state": "警觉",
            "keywords": ["雾港", "潮汐钟", "旧塔"],
        },
        "confidence": 0.95,
        "evidence": "林澈记录异常并行动。",
    },
    {
        "fact_type": "worldbuilding_fact",
        "payload": {
            "title_hint": "雾港潮汐钟",
            "dimension_hint": "culture",
            "keywords": ["潮汐钟", "雾港", "旧塔"],
            "content_points": ["居民依靠潮汐钟判断潮汐", "倒转被视为不祥征兆"],
        },
        "confidence": 0.94,
        "evidence": "镇民称潮汐钟倒转是不祥征兆。",
    },
]


def _stream_chunk(content: str) -> bytes:
    payload = {"choices": [{"delta": {"content": content}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or "0")
        body_text = ""
        if length:
            body_text = self.rfile.read(length).decode("utf-8", errors="ignore")
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return

        payload = FACT_JSONL if "事实抽取器" in body_text else CATALOGING_JSONL
        body = "\n".join(json.dumps(item, ensure_ascii=False) for item in payload) + "\n"
        chunks = [_stream_chunk(piece) for piece in [body[:80], body[80:220], body[220:]] if piece]
        chunks.append(b"data: [DONE]\n\n")
        response_body = b"".join(chunks)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 18080), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
