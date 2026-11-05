"""SQLite engine and session setup (S2-01).

Every connection gets, before any statement runs:

- `PRAGMA foreign_keys = ON` -- SQLite leaves FK enforcement off by default
  and it is per-connection (`docs/DATABASE.md` §5).
- `PRAGMA busy_timeout` -- a short wait instead of an immediate
  "database is locked" if two connections overlap.
- Explicit `BEGIN` handling (the SQLAlchemy-documented pysqlite recipe), so
  transactions -- including Alembic's DDL -- are real SQLite transactions
  that roll back as a unit, instead of the driver's legacy implicit-commit
  behavior around DDL.

The database is a local file only; there is no network listener.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_BUSY_TIMEOUT_MS = 15000

#: Connection execution option: start this connection's transactions with
#: `BEGIN IMMEDIATE` (see `_on_begin`). Used for migrations, so two service
#: processes starting at once (e.g. two app windows) serialize instead of
#: racing to create the schema.
IMMEDIATE_OPTION = "controlgauge_begin_immediate"


def _install_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
        # Hand transaction control to SQLAlchemy (BEGIN emitted below).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        # `BEGIN IMMEDIATE` (opt-in per connection) takes the write lock up
        # front, so a concurrent writer waits on `busy_timeout` instead of
        # failing with SQLITE_BUSY on a read-to-write lock upgrade.
        if connection.get_execution_options().get(IMMEDIATE_OPTION):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("BEGIN")


class Database:
    """Owns the engine and session factory for one SQLite file."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        #: The data directory this database lives in (set by
        #: `initialize_database`); the evidence store sits beside it.
        self.data_dir: Path | None = None
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._write_sessions = sessionmaker(
            bind=engine.execution_options(**{IMMEDIATE_OPTION: True}), expire_on_commit=False
        )

    @classmethod
    def open(cls, path: Path) -> Database:
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        _install_sqlite_pragmas(engine)
        return cls(engine)

    @contextmanager
    def session(self, *, write: bool = False) -> Iterator[Session]:
        """A session committed on success and rolled back on any error.
        `write=True` begins with `BEGIN IMMEDIATE`: use it for any
        transaction that writes, so concurrent writers queue instead of
        failing on a lock upgrade."""
        session = (self._write_sessions if write else self._sessions)()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
