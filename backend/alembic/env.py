"""Alembic environment configuration for source and packaged runtimes."""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.database import models as _models  # noqa: F401
from app.database.session import Base, require_sqlite_foreign_keys

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _prepare_online_connection(connection) -> None:
    """Verify the requested SQLite FK mode without leaving a transaction open."""

    already_in_transaction = connection.in_transaction()
    migration_fk_disabled = bool(
        config.attributes.get("sqlite_migration_foreign_keys_disabled")
    )
    if connection.dialect.name == "sqlite" and migration_fk_disabled:
        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        if int(enabled or 0) != 0:
            raise RuntimeError(
                "SQLite migration connection unexpectedly has foreign keys enabled"
            )
    else:
        require_sqlite_foreign_keys(connection)
    if not already_in_transaction and connection.in_transaction():
        # PRAGMA reads trigger SQLAlchemy autobegin. Alembic must own the
        # standalone migration transaction or its version update is rolled
        # back when the connection context closes.
        connection.commit()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _prepare_online_connection(supplied_connection)
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _prepare_online_connection(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
