"""Transaction boundary used by command-oriented application services."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import Self

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ..database.session import SessionLocal

_DEFERRED_COMMIT_DEPTH_KEY = "siming.deferred_commit_depth"


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


def _deferred_commit_depth(session: Session) -> int:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        return 0
    value = info.get(_DEFERRED_COMMIT_DEPTH_KEY, 0)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def session_commits_deferred(session: Session) -> bool:
    """Return whether this session is inside an outer owned transaction."""

    return _deferred_commit_depth(session) > 0


@contextmanager
def defer_session_commits(session: Session) -> Iterator[None]:
    """Turn nested ``commit_session`` calls into flushes for one session.

    The outer application command still owns the one real commit or rollback.
    The depth marker lives on ``Session.info`` so nested scopes compose while
    unrelated sessions remain completely independent.
    """

    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        raise TypeError("defer_session_commits requires a SQLAlchemy Session")
    previous_depth = _deferred_commit_depth(session)
    info[_DEFERRED_COMMIT_DEPTH_KEY] = previous_depth + 1
    try:
        yield
    finally:
        current_depth = _deferred_commit_depth(session)
        if current_depth <= 1:
            info.pop(_DEFERRED_COMMIT_DEPTH_KEY, None)
        else:
            info[_DEFERRED_COMMIT_DEPTH_KEY] = current_depth - 1


def commit_session(session: Session) -> None:
    """Commit a request-owned legacy session through the UoW boundary.

    This bridge keeps existing route and worker session lifetimes intact while
    ensuring transaction completion has one implementation and rollback path.
    New application commands should receive a UnitOfWork directly.
    """

    if session_commits_deferred(session):
        session.flush()
        return
    with SqlAlchemyUnitOfWork.from_session(session) as uow:
        uow.commit()


def commit_connection(connection: Connection) -> None:
    """Complete a low-level infrastructure transaction at the UoW boundary."""

    connection.commit()


def rollback_connection(connection: Connection) -> None:
    """Roll back a low-level infrastructure transaction at the UoW boundary."""

    connection.rollback()


__all__ = [
    "SqlAlchemyUnitOfWork",
    "UnitOfWork",
    "commit_connection",
    "commit_session",
    "defer_session_commits",
    "rollback_connection",
    "session_commits_deferred",
]
