"""S6-06: the recommendation routes over the SHIPPED manifest.

Structural contract (global contract 1): both routes are pure
computation — cached hardware or a synthetic profile in, verdicts out;
no model child, no live re-detection, no network (offline guard on).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.hardware import GpuInfo, HardwareInfo
from controlgauge_service.hardware.detect import store_hardware_cache

from .conftest import VALID_TOKEN

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}
GIB = 1024**3

REPORT_FIELDS = {"hardware_source", "entries", "pairs"}
ENTRY_FIELDS = {"model_id", "role", "verdict", "load_mode", "estimated", "reasons"}
PAIR_FIELDS = {
    "generation_model_id",
    "embedding_model_id",
    "verdict",
    "combined_total_bytes",
    "reasons",
}
ESTIMATED_FIELDS = {"weights_bytes", "kv_cache_bytes", "runtime_overhead_bytes", "total_bytes"}


#: Deterministic sentinel snapshot: 32 GiB RAM, one 24 GiB GPU, 512 GiB
#: free — every shipped entry fits the GPU path, so all verdicts are
#: `recommended` and every pair `simultaneous` (pinned below).
def _sentinel_info() -> HardwareInfo:
    return HardwareInfo(
        os_name="linux",
        os_version="route-test",
        cpu_model="Route Test CPU",
        cpu_cores_logical=8,
        ram_total_bytes=32 * GIB,
        ram_available_bytes=16 * GIB,
        gpus=(GpuInfo(vendor="nvidia", name="Route Test GPU", vram_bytes=24 * GIB),),
        model_dir_free_bytes=512 * GIB,
        detected_at=datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolated data dir via the documented dev/test override; the
    routes resolve it at request time."""
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=51824,
        environment="production",
        session_token=VALID_TOKEN,
        model_dir=tmp_path / "models",
    )
    return TestClient(create_app(settings))


# --- auth -------------------------------------------------------------------------


def test_recommendations_requires_token(client: TestClient, data_dir: Path) -> None:
    assert client.get("/recommendations").status_code == 401
    assert client.get("/recommendations/profile/consumer-cpu").status_code == 401


# --- detected hardware --------------------------------------------------------------


def test_recommendations_without_cache_is_503(
    client: TestClient, data_dir: Path, offline_guard: None
) -> None:
    """No detection has run: 503 hardware_unavailable (the user runs
    POST /hardware/refresh first), never a fabricated report."""
    response = client.get("/recommendations", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "hardware_unavailable"


def test_recommendations_from_cached_hardware_is_structural(
    client: TestClient, data_dir: Path, offline_guard: None
) -> None:
    store_hardware_cache(data_dir, _sentinel_info())
    response = client.get("/recommendations", headers=AUTH)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert set(body) == REPORT_FIELDS
    assert body["hardware_source"] == "detected"
    assert {e["model_id"] for e in body["entries"]} == {
        "qwen2.5-1.5b-instruct-q4-k-m",
        "qwen2.5-7b-instruct-q4-k-m",
        "qwen2.5-14b-instruct-q4-k-m",
        "nomic-embed-text-v1.5-q4-k-m",
    }
    for entry in body["entries"]:
        assert set(entry) == ENTRY_FIELDS
        assert set(entry["estimated"]) == ESTIMATED_FIELDS
        assert entry["verdict"] == "recommended"
        assert entry["estimated"]["total_bytes"] > 0
    for pair in body["pairs"]:
        assert set(pair) == PAIR_FIELDS
        assert pair["verdict"] == "simultaneous"
    # The generation verdicts carry their best pair's load mode.
    gen = next(e for e in body["entries"] if e["model_id"] == "qwen2.5-7b-instruct-q4-k-m")
    assert gen["load_mode"] == "simultaneous"
    emb = next(e for e in body["entries"] if e["model_id"] == "nomic-embed-text-v1.5-q4-k-m")
    assert emb["load_mode"] is None
    assert emb["role"] == "embedding"


def test_recommendations_never_re_detect(client: TestClient, data_dir: Path) -> None:
    """Serving twice answers identically from the same cache — no probe
    pass, no clock drift in the report (the hardware snapshot is the
    only machine input)."""
    store_hardware_cache(data_dir, _sentinel_info())
    first = client.get("/recommendations", headers=AUTH)
    second = client.get("/recommendations", headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


# --- synthetic profiles ---------------------------------------------------------------


def test_all_three_shipped_profiles_answer(
    client: TestClient, data_dir: Path, offline_guard: None
) -> None:
    for profile_id in ("consumer-cpu", "workstation-32gb", "enterprise-288gb"):
        response = client.get(f"/recommendations/profile/{profile_id}", headers=AUTH)
        assert response.status_code == 200, (profile_id, response.text)
        body = response.json()
        assert body["hardware_source"] == f"profile:{profile_id}"
        assert set(body) == REPORT_FIELDS


def test_enterprise_profile_recommends_the_full_catalog(
    client: TestClient, data_dir: Path, offline_guard: None
) -> None:
    """The no-ceiling evidence over the wire: 8×36 GiB recommends every
    shipped entry."""
    response = client.get("/recommendations/profile/enterprise-288gb", headers=AUTH)
    body = response.json()
    assert all(entry["verdict"] == "recommended" for entry in body["entries"])


def test_unknown_profile_is_404(client: TestClient, data_dir: Path, offline_guard: None) -> None:
    response = client.get("/recommendations/profile/no-such-profile", headers=AUTH)
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "profile_unknown"
    assert body["error"].get("fields", []) == []
