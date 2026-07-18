"""Composition boundary for cataloging query operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..application.cataloging_queries import CatalogingQueries

CatalogingQueryFactory = Callable[[Any], CatalogingQueries]
_factory: CatalogingQueryFactory | None = None


def configure_cataloging_queries(factory: CatalogingQueryFactory) -> None:
    global _factory
    _factory = factory


def cataloging_queries(session: Any) -> CatalogingQueries:
    if _factory is None:
        raise RuntimeError("Cataloging query dependencies have not been configured")
    return _factory(session)


__all__ = ["cataloging_queries", "configure_cataloging_queries"]
