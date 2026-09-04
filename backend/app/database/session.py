"""Database session management."""
import contextlib
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from ..core.config import get_settings
from .write_coordination import install_sqlite_write_coordination

settings = get_settings()


def _is_sqlite_url(database_url: str) -> bool:
    return str(database_url or "").lower().startswith("sqlite")


def configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any = None) -> None:
    """Install and verify the invariants required by every SQLite session.

    SQLite foreign-key enforcement is connection-local and disabled by default.
    The application relies on ``ON DELETE`` ownership cascades, so accepting a
    connection where the pragma did not take effect would silently retain
    cross-owner/orphan rows.  Fail the connection instead.
    """

    import sqlite3

    dbapi_connection.execute("PRAGMA foreign_keys=ON")
    foreign_keys = dbapi_connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0] or 0) != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
    # Apply the wait budget before journal setup: setting WAL can itself briefly
    # contend with another process opening the same database.
    dbapi_connection.execute("PRAGMA busy_timeout=30000")
    # WAL is persistent and normally already configured. A transient reader
    # must not make a new connection fail during startup.
    with contextlib.suppress(sqlite3.OperationalError):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")


def require_sqlite_foreign_keys(connection: Any) -> None:
    """Enable and verify foreign keys on an Alembic SQLAlchemy connection."""

    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    if int(enabled or 0) != 1:
        raise RuntimeError("SQLite migration connection has foreign keys disabled")


def create_session_engine(database_url: str) -> Engine:
    """Create an engine with the same connection policy as the production app."""

    sqlite = _is_sqlite_url(database_url)
    parsed_url = make_url(database_url)
    sqlite_memory = sqlite and (
        parsed_url.database in {None, "", ":memory:"}
        or parsed_url.query.get("mode") == "memory"
    )
    engine_options: dict[str, Any] = {
        "connect_args": {"check_same_thread": False, "timeout": 30} if sqlite else {},
        "echo": False,
        "pool_pre_ping": True,
    }
    if sqlite_memory:
        # A single DBAPI connection keeps the in-memory database coherent and
        # avoids QueuePool-only arguments rejected by SingletonThreadPool.
        engine_options["poolclass"] = StaticPool
    else:
        engine_options.update(
            pool_size=20,
            max_overflow=30,
            pool_timeout=10,
            pool_recycle=300,
        )
    managed_engine = create_engine(database_url, **engine_options)
    if sqlite:
        event.listen(managed_engine, "connect", configure_sqlite_connection)
    return managed_engine


engine = create_session_engine(settings.database_url)


database_write_coordinator = install_sqlite_write_coordination(
    engine,
    settings.database_url,
    timeout=30,
)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Yield a database session for dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
