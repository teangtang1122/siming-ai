"""FastAPI dependency for the prompt-pack catalog."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ....database.session import get_db
from ..application.prompt_packs import PromptPackCatalog

PromptPackCatalogFactory = Callable[[Session], PromptPackCatalog]
_factory: PromptPackCatalogFactory | None = None


def configure_prompt_pack_dependencies(factory: PromptPackCatalogFactory) -> None:
    global _factory
    _factory = factory


def get_prompt_pack_catalog(
    db: Annotated[Session, Depends(get_db)],
) -> PromptPackCatalog:
    if _factory is None:
        raise RuntimeError("Prompt-pack dependencies have not been configured")
    return _factory(db)


__all__ = ["configure_prompt_pack_dependencies", "get_prompt_pack_catalog"]
