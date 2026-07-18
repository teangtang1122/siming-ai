"""Compatibility exports for legacy ``app.ai`` imports.

The gateway is resolved lazily so importing an adapter does not load the
model-runtime infrastructure and create a package initialization cycle.
"""

from typing import Any

from .base import BaseAdapter


def __getattr__(name: str) -> Any:
    if name == "LLMGateway":
        from .gateway import LLMGateway

        return LLMGateway
    raise AttributeError(name)

__all__ = ["LLMGateway", "BaseAdapter"]
