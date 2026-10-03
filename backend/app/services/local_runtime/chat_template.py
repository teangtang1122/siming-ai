"""Chat-template options shared by local inference and token budgeting."""

from __future__ import annotations


def local_chat_template_kwargs(model_key: str, *, tools_enabled: bool) -> dict[str, bool]:
    # Keep this decision identical for the actual request and the llama.cpp
    # template probe used by the request budget.
    return {"enable_thinking": tools_enabled and model_key == "qwen3.8-27b-q3"}


__all__ = ["local_chat_template_kwargs"]
