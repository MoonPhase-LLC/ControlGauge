"""S6-03: the GET /model-catalog route over the shipped manifest.

The structural contract (global contract 1) is pinned here: the answer is
the strictly loaded manifest plus one local database read for installed
state -- no model child, no network (the offline guard is active for the
shape test). Installed state is derived from `model_file.sha256_hash`
matches; a partial shard set is deliberately NOT installed. Responses
carry catalog metadata only: no local path ever appears (T-10).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import Database, initialize_database
from controlgauge_service.db.models import ModelFile

from .conftest import VALID_TOKEN

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}

#: The closed response schema: exactly these fields, in every entry.
ENTRY_FIELDS = frozenset(
    {
        "model_id",
        "display_name",
        "format",
        "backend",
        "roles",
        "tier",
        "recommended",
        "recommendation_withheld_reason",
        "context_window",
        "curated_context_tokens",
        "max_input_tokens",
        "max_output_tokens",
        "total_bytes",
        "license",
        "revision",
        "repository_url",
        "embedding_dimensions",
        "installed",
        "installed_model_ids",
        "evaluation_status",
    }
)

EXPECTED_MODEL_IDS = (
    "qwen2.5-1.5b-instruct-q4-k-m",
    "qwen2.5-7b-instruct-q4-k-m",
    "qwen2.5-14b-instruct-q4-k-m",
    "nomic-embed-text-v1.5-q4-k-m",
)
EXPECTED_TIERS = ("FAST", "BALANCED", "ACCURATE", None)
#: Per-entry declared download footprints (sum of the entry's files).
EXPECTED_TOTALS = {
    "qwen2.5-1.5b-instruct-q4-k-m": 1117320736,
    "qwen2.5-7b-instruct-q4-k-m": 4683073632,
    "qwen2.5-14b-instruct-q4-k-m": 8988110496,
    "nomic-embed-text-v1.5-q4-k-m": 84106624,
}
#: Pinned SHA-256s the installed-state tests seed rows against.
SHAS = {
    "1.5b": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
    "7b-shard1": "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db",
    "7b-shard2": "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a",
    "14b-shard1": "a09ea5e7b1eafb1b30b241726c3cc3c905c96f14ad41e246ffa5f44e53904f68",
    "14b-shard2": "21b9457d079680d284e90ef69607c4b2d8ef64a09d4729cb7b5e1357bdba41ae",
    "14b-shard3": "c8d37006760a387a35216e070e6664d7da927f10be8eb870fef2e3d4833d9976",
    "nomic": "d4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac",
}


@pytest.fixture()
def catalog_env(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """App + client + database. No model runtime: the route is structural
    (manifest + DB read) and must never touch a child."""
    database = initialize_database(tmp_path)
    app = create_app(Settings(session_token=VALID_TOKEN), database=database)
    client = TestClient(app)
    yield SimpleNamespace(client=client, database=database, tmp_path=tmp_path)
    client.close()
    database.dispose()


def _seed_model(
    database: Database,
    sha256: str,
    size_bytes: int,
    tmp_path: Path,
    role: str = "generation",
) -> str:
    """One imported model_file row with the given content identity."""
    model_id = uuid.uuid4().hex
    with database.session() as session:
        session.add(
            ModelFile(
                id=model_id,
                role=role,
                display_name="Seeded Model",
                path=str(tmp_path / "seeded.gguf"),
                sha256_hash=sha256,
                size_bytes=size_bytes,
            )
        )
        session.commit()
    return model_id


def _entries_by_id(client: TestClient) -> dict[str, dict[str, object]]:
    response = client.get("/model-catalog", headers=AUTH)
    assert response.status_code == 200, response.text
    return {entry["model_id"]: entry for entry in response.json()["entries"]}


# --- auth ------------------------------------------------------------------------


def test_catalog_requires_token(catalog_env: SimpleNamespace) -> None:
    response = catalog_env.client.get("/model-catalog")
    assert response.status_code == 401


# --- shape (offline-guarded) ------------------------------------------------------


def test_catalog_shape_offline(catalog_env: SimpleNamespace, offline_guard: None) -> None:
    """The full structural contract under the offline guard: the route
    answers from the shipped manifest + one local DB read only."""
    response = catalog_env.client.get("/model-catalog", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"catalog_version", "entries"}
    assert body["catalog_version"] == "1.0.0"
    assert len(body["entries"]) == 4
    entries = {entry["model_id"]: entry for entry in body["entries"]}
    assert tuple(entries) == EXPECTED_MODEL_IDS
    for model_id, entry in entries.items():
        assert set(entry) == ENTRY_FIELDS
        assert entry["evaluation_status"] == "unevaluated"
        assert entry["recommended"] is True
        assert entry["recommendation_withheld_reason"] is None
        assert entry["total_bytes"] == EXPECTED_TOTALS[model_id]
    assert tuple(entry["tier"] for entry in entries.values()) == EXPECTED_TIERS
    # Nothing about the local machine or database leaks (T-10): no local
    # path appears anywhere in the serialized response.
    assert str(catalog_env.tmp_path) not in response.text
    # The embedding entry carries the embedding triple; generation
    # entries carry none.
    nomic = entries["nomic-embed-text-v1.5-q4-k-m"]
    assert nomic["embedding_dimensions"] == 768
    for model_id in EXPECTED_MODEL_IDS[:3]:
        assert entries[model_id]["embedding_dimensions"] is None
    # Empty database: nothing installed.
    for entry in entries.values():
        assert entry["installed"] is False
        assert entry["installed_model_ids"] == []


# --- installed state --------------------------------------------------------------


def test_installed_single_file_entry(catalog_env: SimpleNamespace) -> None:
    """Seeding the nomic SHA-256 installs exactly that entry."""
    nomic_id = _seed_model(
        catalog_env.database, SHAS["nomic"], 84106624, catalog_env.tmp_path, role="embedding"
    )
    entries = _entries_by_id(catalog_env.client)
    assert entries["nomic-embed-text-v1.5-q4-k-m"]["installed"] is True
    assert entries["nomic-embed-text-v1.5-q4-k-m"]["installed_model_ids"] == [nomic_id]
    for model_id in EXPECTED_MODEL_IDS[:3]:
        assert entries[model_id]["installed"] is False
        assert entries[model_id]["installed_model_ids"] == []


def test_partial_shard_set_not_installed(catalog_env: SimpleNamespace) -> None:
    """One 7B shard imported: the entry reports the row (it matched a
    declared file) but is NOT installed -- a partial shard set cannot
    run."""
    shard_id = _seed_model(
        catalog_env.database, SHAS["7b-shard1"], 3993201344, catalog_env.tmp_path
    )
    entry = _entries_by_id(catalog_env.client)["qwen2.5-7b-instruct-q4-k-m"]
    assert entry["installed"] is False
    assert entry["installed_model_ids"] == [shard_id]


def test_full_shard_set_installed(catalog_env: SimpleNamespace) -> None:
    """Both 7B shards imported: installed, with both rows listed."""
    shard_ids = [
        _seed_model(catalog_env.database, sha, size, catalog_env.tmp_path)
        for sha, size in ((SHAS["7b-shard1"], 3993201344), (SHAS["7b-shard2"], 689872288))
    ]
    entry = _entries_by_id(catalog_env.client)["qwen2.5-7b-instruct-q4-k-m"]
    assert entry["installed"] is True
    assert entry["installed_model_ids"] == sorted(shard_ids)


def test_unrelated_hash_never_matches(catalog_env: SimpleNamespace) -> None:
    """A user-imported model unrelated to the catalog installs nothing."""
    _seed_model(catalog_env.database, "ab" * 32, 1234, catalog_env.tmp_path)
    entries = _entries_by_id(catalog_env.client)
    for entry in entries.values():
        assert entry["installed"] is False
        assert entry["installed_model_ids"] == []


# --- fail-closed manifest ---------------------------------------------------------


def test_catalog_unavailable_is_503(
    catalog_env: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing shipped manifest is a fixed 503 ``catalog_unavailable``
    through the REAL loader (the frozen-root branch), never a partial
    list and never a path in the body."""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    response = catalog_env.client.get("/model-catalog", headers=AUTH)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "catalog_unavailable"
    assert str(tmp_path) not in response.text
    assert "models_catalog" not in response.text
