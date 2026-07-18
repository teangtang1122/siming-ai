"""Persistence operations used by the model configuration HTTP interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class ModelConfigCrud(Protocol):
    def list_configs(self) -> Sequence[Any]: ...

    def get_provider(self, provider: str) -> Any | None: ...

    def create(self, **values: Any) -> Any: ...

    def delete(self, config: Any) -> None: ...

    def get_global(self) -> Any | None: ...

    def get_ready_global(self) -> Any | None: ...

    def clear_global(self) -> None: ...

    def list_local_models(self) -> Sequence[Any]: ...


__all__ = ["ModelConfigCrud"]
