"""Transaction boundary used by command-oriented application services."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session

from ..database.session import SessionLocal


class UnitOfWork(ABC):
    """Application-level transaction contract."""

    session: Session

    @abstractmethod
    def __enter__(self) -> Self:
        raise NotImplementedError

    @abstractmethod
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def flush(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def commit(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def rollback(self) -> None:
        raise NotImplementedError


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Own one SQLAlchemy session and make commit an explicit use-case action."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        close_session: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._close_session = close_session
        self._committed = False
        self.session = None  # type: ignore[assignment]

    @classmethod
    def from_session(cls, session: Session) -> SqlAlchemyUnitOfWork:
        """Bind a request-owned session without taking over its lifecycle."""

        return cls(lambda: session, close_session=False)

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self._close_session:
                self.session.close()

    def flush(self) -> None:
        self.session.flush()

    def commit(self) -> None:
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        self.session.rollback()


def commit_session(session: Session) -> None:
    """Commit a request-owned legacy session through the UoW boundary.

    This bridge keeps existing route and worker session lifetimes intact while
    ensuring transaction completion has one implementation and rollback path.
    New application commands should receive a UnitOfWork directly.
    """

    with SqlAlchemyUnitOfWork.from_session(session) as uow:
        uow.commit()


__all__ = ["SqlAlchemyUnitOfWork", "UnitOfWork", "commit_session"]
