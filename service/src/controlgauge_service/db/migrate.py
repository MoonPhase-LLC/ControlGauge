"""Programmatic Alembic upgrades (S2-01).

The service upgrades its own database to the latest revision at startup,
before it accepts any request. Migrations live inside the package
(`controlgauge_service/migrations/`) so the packaged (PyInstaller) service
carries them too -- `scripts/package-windows.ps1` adds that directory as
data. There is no ad hoc schema creation (`metadata.create_all`) anywhere:
the versioned migrations are the only way the schema is built
(`docs/DATABASE.md` §6).
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

# `migrations/env.py` ships as a data file, so PyInstaller cannot see its
# imports. Importing the models here puts them in the frozen archive (and
# registers every table on `Base.metadata` before env.py reads it).
from . import base, models  # noqa: F401
from .engine import IMMEDIATE_OPTION, Database

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:
        raise RuntimeError("no_migration_head")
    return head


def current_revision(database: Database) -> str | None:
    with database.engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade_to_head(database: Database) -> str:
    """Applies every pending migration in one transaction and returns the
    resulting revision. Alembic's own INFO logging is quieted: it is noise
    in the service's structured log and carries nothing the caller does not
    already report."""
    logging.getLogger("alembic").setLevel(logging.WARNING)
    config = _config()
    with database.engine.connect() as raw:
        connection = raw.execution_options(**{IMMEDIATE_OPTION: True})
        with connection.begin():
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    revision = current_revision(database)
    if revision is None:
        raise RuntimeError("migration_did_not_apply")
    return revision
