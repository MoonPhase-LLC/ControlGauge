"""Startup database initialization: resolve location, open, migrate.

Runs before the service accepts any request, in both standalone and
supervised modes. Any failure raises `DatabaseInitError` with a fixed,
path-free reason; callers fail closed (the process exits rather than
serving without its database).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import storage
from .engine import Database
from .migrate import upgrade_to_head

logger = logging.getLogger("controlgauge_service.db")


class DatabaseInitError(RuntimeError):
    """Startup could not produce a migrated database. `reason` is a fixed
    category string, safe to log and to report over the private channel."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def initialize_database(data_dir: Path | None = None) -> Database:
    try:
        resolved = data_dir if data_dir is not None else storage.resolve_data_dir()
        storage.ensure_data_dir(resolved)
    except storage.StorageLocationError as exc:
        raise DatabaseInitError(str(exc)) from None

    database = Database.open(storage.database_path(resolved))
    try:
        revision = upgrade_to_head(database)
    except Exception as exc:
        database.dispose()
        logger.error("database_migration_failed", extra={"exc_type": type(exc).__name__})
        raise DatabaseInitError("database_migration_failed") from None

    # Framework *metadata* only (PD-006); imported lazily to keep this
    # module free of an import cycle with the catalog's model imports.
    from ..framework_catalog import sync_from_default_location

    try:
        sync_from_default_location(database)
    except Exception as exc:
        database.dispose()
        logger.error("framework_sync_failed", extra={"exc_type": type(exc).__name__})
        raise DatabaseInitError("framework_sync_failed") from None

    logger.info("database_ready", extra={"schema_revision": revision})
    return database
