"""Composition boundary for model configuration CRUD."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.config_crud import ModelConfigCrud

ModelConfigCrudFactory = Callable[[Any], ModelConfigCrud]
_factory: ModelConfigCrudFactory | None = None


def configure_model_config_crud(factory: ModelConfigCrudFactory) -> None:
    global _factory
    _factory = factory


def model_config_crud(session: Any) -> ModelConfigCrud:
    if _factory is None:
        raise RuntimeError("Model configuration CRUD has not been configured")
    return _factory(session)


__all__ = ["configure_model_config_crud", "model_config_crud"]
