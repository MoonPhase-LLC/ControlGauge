"""S2-01: engine setup, migrations from scratch, fail-closed initialization."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from controlgauge_service import storage
from controlgauge_service.db import Database, DatabaseInitError, initialize_database
from controlgauge_service.db.migrate import current_revision, head_revision, upgrade_to_head


def test_fresh_directory_is_created_and_migrated_to_head(tmp_path: Path) -> None:
    data_dir = tmp_path / "new" / "data"
    database = initialize_database(data_dir)
    try:
        assert storage.database_path(data_dir).is_file()
        assert current_revision(database) == head_revision()
    finally:
        database.dispose()


def test_migration_is_idempotent_and_reproducible(tmp_path: Path) -> None:
    first = initialize_database(tmp_path / "one")
    second = initialize_database(tmp_path / "two")
    try:
        assert upgrade_to_head(first) == head_revision()  # re-running is a no-op
        assert current_revision(first) == current_revision(second) == head_revision()
    finally:
        first.dispose()
        second.dispose()


def test_concurrent_startups_on_one_fresh_database_both_succeed(tmp_path: Path) -> None:
    """Two service processes starting together (e.g. two app windows) must
    not race the first migration into a failure (`BEGIN IMMEDIATE`)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        databases = list(pool.map(lambda _: initialize_database(tmp_path), range(4)))
    try:
        assert all(current_revision(db) == head_revision() for db in databases)
    finally:
        for db in databases:
            db.dispose()


def test_resolves_location_from_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(storage.DATA_DIR_ENV_VAR, str(tmp_path))
    database = initialize_database()
    try:
        assert storage.database_path(tmp_path).is_file()
    finally:
        database.dispose()


def test_every_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    database = initialize_database(tmp_path)
    try:
        for _ in range(3):
            with database.engine.connect() as connection:
                assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        database.dispose()


def test_session_rolls_back_on_error(tmp_path: Path) -> None:
    database = initialize_database(tmp_path)
    try:
        with database.session() as session:
            session.execute(text("CREATE TABLE scratch (v INTEGER)"))
        with pytest.raises(RuntimeError), database.session() as session:
            session.execute(text("INSERT INTO scratch VALUES (1)"))
            raise RuntimeError("boom")
        with database.session() as session:
            assert session.execute(text("SELECT COUNT(*) FROM scratch")).scalar() == 0
    finally:
        database.dispose()


def test_unusable_location_fails_closed_without_the_path(tmp_path: Path) -> None:
    blocker = tmp_path / "private-user-dir"
    blocker.write_text("not a directory")
    with pytest.raises(DatabaseInitError) as info:
        initialize_database(blocker)
    assert info.value.reason in {"data_dir_create_failed", "data_dir_not_a_directory"}
    assert "private-user-dir" not in str(info.value)


def test_corrupt_database_fails_closed(tmp_path: Path) -> None:
    storage.database_path(tmp_path).write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(DatabaseInitError) as info:
        initialize_database(tmp_path)
    assert info.value.reason == "database_migration_failed"


def test_database_from_a_newer_app_version_fails_closed(tmp_path: Path) -> None:
    """A schema revision this build does not know (e.g. after a downgrade)
    must stop startup rather than run against an unknown schema."""
    database = initialize_database(tmp_path)
    database.dispose()
    with sqlite3.connect(storage.database_path(tmp_path)) as raw:
        raw.execute("UPDATE alembic_version SET version_num = '9999_from_the_future'")
    with pytest.raises(DatabaseInitError):
        initialize_database(tmp_path)


def test_ready_log_reports_revision_but_not_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data_dir = tmp_path / "user-name-marker"
    with caplog.at_level(logging.INFO, logger="controlgauge_service.db"):
        database = initialize_database(data_dir)
    database.dispose()
    records = [r for r in caplog.records if r.getMessage() == "database_ready"]
    assert records and getattr(records[0], "schema_revision", None) == head_revision()
    assert "user-name-marker" not in caplog.text


def test_open_uses_given_file(tmp_path: Path) -> None:
    path = tmp_path / "explicit.db"
    database = Database.open(path)
    try:
        upgrade_to_head(database)
        assert path.is_file()
    finally:
        database.dispose()
