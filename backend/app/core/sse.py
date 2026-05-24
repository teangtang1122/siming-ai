"""SSE (Server-Sent Events) formatting utility."""
from __future__ import annotations

import json


def sse_event(payload) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"
