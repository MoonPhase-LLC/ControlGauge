from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings

#: 32 chars -- comfortably above MIN_SESSION_TOKEN_LENGTH. Not a secret:
#: this value is fixed only so tests are deterministic and never resembles
#: a value that could be mistaken for a real, in-use credential.
VALID_TOKEN = "test-only-session-token-abcdef12"

SettingsFactory = Callable[..., Settings]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--service-exe",
        default=None,
        help=(
            "Run the supervised-process tests against this packaged service executable "
            "(S1-09; see docs/PACKAGING.md) instead of `python -m controlgauge_service`."
        ),
    )


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strips any ambient `CONTROLGAUGE_*` variables from the real shell so
    the suite's behavior never depends on what happens to be set on the
    machine running it -- every test builds `Settings` from explicit kwargs
    or its own `monkeypatch.setenv` calls instead."""
    import os

    for key in list(os.environ):
        if key.startswith("CONTROLGAUGE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Builds a `Settings` instance from explicit kwargs only.

    Deliberately does not fall back to the real process environment for
    any field under test, so tests are isolated from whatever
    `CONTROLGAUGE_*` variables happen to be set on the machine running
    them.
    """

    def _make(
        *,
        host: str = "127.0.0.1",
        port: int = 51823,
        environment: str = "production",
        session_token: str | None = VALID_TOKEN,
    ) -> Settings:
        return Settings(
            host=host,
            port=port,
            environment=environment,
            session_token=session_token,
        )

    return _make


@pytest.fixture
def make_app(make_settings: SettingsFactory) -> Callable[..., FastAPI]:
    def _make(**settings_kwargs: object) -> FastAPI:
        settings = make_settings(**settings_kwargs)
        return create_app(settings)

    return _make


@pytest.fixture
def make_client(make_app: Callable[..., FastAPI]) -> Callable[..., TestClient]:
    def _make(**settings_kwargs: object) -> TestClient:
        return TestClient(make_app(**settings_kwargs))

    return _make


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """The common case: production-mode app with a valid token configured."""
    return make_client()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


SeedDataDir = Callable[[Path], Path]


@pytest.fixture(scope="session")
def catalog_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database migrated to head with the shipped framework catalogs
    imported, built once per test session. Importing a full catalog takes
    about a second, so tests that only need *a* ready database start from a
    copy of this one; tests about migration and import themselves
    (`test_db_foundation.py`, `test_catalog_import.py`, the restart test in
    `test_supervised_process.py`) still start from an empty directory."""
    from controlgauge_service import storage
    from controlgauge_service.db import initialize_database

    directory = tmp_path_factory.mktemp("catalog-template")
    initialize_database(directory).dispose()
    return storage.database_path(directory)


@pytest.fixture
def seed_data_dir(catalog_template: Path) -> SeedDataDir:
    """Copies the template database into `data_dir` (created if needed)."""

    def _seed(data_dir: Path) -> Path:
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(catalog_template, data_dir / catalog_template.name)
        return data_dir

    return _seed
