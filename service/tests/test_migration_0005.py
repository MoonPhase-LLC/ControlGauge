"""S5-05: migration `0005_model_files` applies and rolls back cleanly.

Since S6-05 the migration head is `0006_model_registry`, so this test
verifies the 0006 head shape carries the full 0005 column set (plus the
registry columns) and downgrades explicitly to `0004_artifacts` -- the
two-step downgrade passes back through 0005's drop of `model_file`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from controlgauge_service import storage
from controlgauge_service.db import Database, initialize_database
from controlgauge_service.db.engine import IMMEDIATE_OPTION
from controlgauge_service.db.migrate import (
    _config,
    current_revision,
    head_revision,
)


def _downgrade_to(database: Database, target: str) -> None:
    """An explicit alembic downgrade on the database's own connection
    (the same connection-passing pattern `upgrade_to_head` uses)."""
    config = _config()
    with database.engine.connect() as raw:
        connection = raw.execution_options(**{IMMEDIATE_OPTION: True})
        with connection.begin():
            config.attributes["connection"] = connection
            command.downgrade(config, target)


def test_migration_0005_applies_and_rolls_back(tmp_path: Path) -> None:
    assert head_revision() == "0006_model_registry"
    database = initialize_database(tmp_path)
    try:
        assert current_revision(database) == "0006_model_registry"
        with database.engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(model_file)"))}
        # The spec'd 0005 column list (docs/DATABASE.md §10) plus the
        # S6-05 registry columns (origin/verification).
        assert columns == {
            "id",
            "role",
            "display_name",
            "path",
            "sha256_hash",
            "size_bytes",
            "imported_at",
            "last_verified_at",
            "embedding_dimensions",
            "embedding_normalization",
            "embedding_similarity_metric",
            "origin",
            "verification",
        }
        # Unique (sha256_hash, role) is the duplicate rule.
        with database.engine.connect() as connection:
            indexes = connection.execute(text("PRAGMA index_list(model_file)")).fetchall()
        assert any(row[2] for row in indexes), "expected a unique index on (sha256_hash, role)"
        # The role domain is enforced by the schema.
        with pytest.raises(IntegrityError):
            with database.engine.connect() as connection:
                connection.execute(
                    text(
                        "INSERT INTO model_file (id, role, display_name, path, sha256_hash, "
                        "size_bytes, imported_at) VALUES ('x', 'sampler', 'n', '/p', "
                        "'0', 1, '2026-11-17T00:00:00')"
                    )
                )
    finally:
        database.dispose()

    _downgrade_to(Database.open(storage.database_path(tmp_path)), "0004_artifacts")
    # Opened WITHOUT migrating: the downgrade must be observable as such.
    database = Database.open(storage.database_path(tmp_path))
    try:
        assert current_revision(database) == "0004_artifacts"
        with database.engine.connect() as connection:
            names = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='model_file'")
            ).fetchall()
        assert names == []
    finally:
        database.dispose()
