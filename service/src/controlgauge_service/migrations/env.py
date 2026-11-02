"""Alembic environment for the ControlGauge local database.

Online mode only. The service passes its own connection in
`config.attributes["connection"]` (`db/migrate.py`); the developer CLI
(`service/alembic.ini`) falls back to `sqlalchemy.url`.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

from controlgauge_service.db import models  # noqa: F401 -- registers tables on Base.metadata
from controlgauge_service.db.base import Base

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite cannot ALTER most things in place
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not configured")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON")
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported")
run_migrations_online()
