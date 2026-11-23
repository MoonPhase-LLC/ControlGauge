"""S6-05: migration `0006_model_registry` applies, backfills, and rolls
back cleanly.

Seeds a Sprint 5 (0005) database with `model_file` rows, upgrades to
head, and asserts the registry distinction exists: origin/verification
backfilled, `model_source` 1:1 with correct `files_json`, the schema
matches the ORM metadata (`alembic check`), the new CHECK domains refuse
bad values, and a downgrade removes the new tables cleanly.
"""

from __future__ import annotations

import json
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
)


def _upgrade_to(database: Database, target: str) -> None:
    config = _config()
    with database.engine.connect() as raw:
        connection = raw.execution_options(**{IMMEDIATE_OPTION: True})
        with connection.begin():
            config.attributes["connection"] = connection
            command.upgrade(config, target)


def _downgrade_to(database: Database, target: str) -> None:
    config = _config()
    with database.engine.connect() as raw:
        connection = raw.execution_options(**{IMMEDIATE_OPTION: True})
        with connection.begin():
            config.attributes["connection"] = connection
            command.downgrade(config, target)


def _assert_no_pending_schema_changes(database: Database) -> None:
    """`alembic check`: the ORM metadata and the migrated schema agree."""
    config = _config()
    with database.engine.connect() as raw:
        connection = raw.execution_options(**{IMMEDIATE_OPTION: True})
        config.attributes["connection"] = connection
        command.check(config)


_SEED = (
    "INSERT INTO model_file (id, role, display_name, path, sha256_hash, size_bytes, "
    "imported_at) VALUES (:id, :role, :display_name, :path, :sha256_hash, :size_bytes, "
    "'2026-11-01T10:00:00')"
)


@pytest.fixture()
def seeded_0005(tmp_path: Path) -> Database:
    """A database at exactly `0005_model_files` with two seeded rows."""
    database = Database.open(storage.database_path(tmp_path))
    _upgrade_to(database, "0005_model_files")
    with database.engine.begin() as connection:
        connection.execute(
            text(_SEED),
            {
                "id": "gen-1",
                "role": "generation",
                "display_name": "gen.gguf",
                "path": "/models/store/gen.gguf",
                "sha256_hash": "a" * 64,
                "size_bytes": 1_000_000,
            },
        )
        connection.execute(
            text(_SEED),
            {
                "id": "emb-1",
                "role": "embedding",
                "display_name": "emb.gguf",
                "path": "/models/store/nested/emb.gguf",
                "sha256_hash": "b" * 64,
                "size_bytes": 2_000_000,
            },
        )
    return database


def test_migration_0006_backfills_registry(seeded_0005: Database, tmp_path: Path) -> None:
    try:
        assert current_revision(seeded_0005) == "0005_model_files"
        _upgrade_to(seeded_0005, "0006_model_registry")
        assert current_revision(seeded_0005) == "0006_model_registry"

        with seeded_0005.engine.connect() as connection:
            rows = [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT mf.id, mf.origin, mf.verification, ms.model_file_id, ms.origin, "
                        "ms.catalog_model_id, ms.files_json FROM model_file mf "
                        "LEFT JOIN model_source ms ON ms.model_file_id = mf.id ORDER BY mf.id"
                    )
                ).fetchall()
            ]
        # Every Sprint 5 row backfilled as local_import/none -- an honest
        # description of a pre-0006 import.
        assert rows == [
            (
                "emb-1",
                "local_import",
                "none",
                "emb-1",
                "local_import",
                None,
                json.dumps([{"filename": "emb.gguf", "sha256": "b" * 64, "size_bytes": 2_000_000}]),
            ),
            (
                "gen-1",
                "local_import",
                "none",
                "gen-1",
                "local_import",
                None,
                json.dumps([{"filename": "gen.gguf", "sha256": "a" * 64, "size_bytes": 1_000_000}]),
            ),
        ]
        # The backfill's filename is the path's last segment (nested path).
        files = json.loads(rows[0][6])
        assert files[0]["filename"] == "emb.gguf"

        # The migrated schema matches the ORM metadata exactly.
        _assert_no_pending_schema_changes(seeded_0005)
    finally:
        seeded_0005.dispose()

    # Re-opened at head through the normal startup path: still clean.
    database = initialize_database(tmp_path)
    try:
        assert current_revision(database) == "0006_model_registry"
        _assert_no_pending_schema_changes(database)
    finally:
        database.dispose()


def test_migration_0006_check_domains_refuse_bad_values(tmp_path: Path) -> None:
    database = initialize_database(tmp_path)
    try:
        with database.engine.connect() as connection:
            # model_file.origin domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO model_file (id, role, display_name, path, sha256_hash, "
                        "size_bytes, imported_at, origin) VALUES ('x', 'generation', 'n', '/p', "
                        ":sha, 1, '2026-11-01T10:00:00', 'bittorrent')"
                    ),
                    {"sha": "c" * 64},
                )
            connection.rollback()
            # model_file.verification domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO model_file (id, role, display_name, path, sha256_hash, "
                        "size_bytes, imported_at, verification) VALUES ('x', 'generation', 'n', "
                        "'/p', :sha, 1, '2026-11-01T10:00:00', 'vibes')"
                    ),
                    {"sha": "c" * 64},
                )
            connection.rollback()
            # model_source.origin domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO model_source (id, model_file_id, origin, files_json, "
                        "created_at, updated_at) VALUES ('s1', 'x', 'bittorrent', '[]', "
                        "'2026-11-01T10:00:00', '2026-11-01T10:00:00')"
                    )
                )
            connection.rollback()
            # external_endpoint.backend domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO external_endpoint (id, display_name, backend, base_url, "
                        "capabilities, registered_at) VALUES ('e1', 'Local', 'gopher', "
                        "'http://127.0.0.1:8080', 'generation', '2026-11-01T10:00:00')"
                    )
                )
            connection.rollback()
            # external_endpoint.capabilities domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO external_endpoint (id, display_name, backend, base_url, "
                        "capabilities, registered_at) VALUES ('e1', 'Local', 'llama_cpp', "
                        "'http://127.0.0.1:8080', 'everything', '2026-11-01T10:00:00')"
                    )
                )
            connection.rollback()
            # external_endpoint.last_health_state domain.
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO external_endpoint (id, display_name, backend, base_url, "
                        "capabilities, registered_at, last_health_state) VALUES "
                        "('e1', 'Local', 'llama_cpp', 'http://127.0.0.1:8080', 'generation', "
                        "'2026-11-01T10:00:00', 'excellent')"
                    )
                )
            connection.rollback()
    finally:
        database.dispose()


def test_migration_0006_downgrades_cleanly(seeded_0005: Database) -> None:
    try:
        _upgrade_to(seeded_0005, "0006_model_registry")
        _downgrade_to(seeded_0005, "0005_model_files")
        assert current_revision(seeded_0005) == "0005_model_files"
        with seeded_0005.engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        assert "model_source" not in names
        assert "external_endpoint" not in names
        # The 0005 shape is restored (no registry columns) and the rows
        # survived the round trip.
        with seeded_0005.engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(model_file)"))}
        assert "origin" not in columns
        assert "verification" not in columns
        rows = [
            tuple(row)
            for row in seeded_0005.engine.connect()
            .execute(
                text(
                    "SELECT id, role, display_name, sha256_hash, size_bytes "
                    "FROM model_file ORDER BY id"
                )
            )
            .fetchall()
        ]
        assert rows == [
            ("emb-1", "embedding", "emb.gguf", "b" * 64, 2_000_000),
            ("gen-1", "generation", "gen.gguf", "a" * 64, 1_000_000),
        ]
    finally:
        seeded_0005.dispose()
