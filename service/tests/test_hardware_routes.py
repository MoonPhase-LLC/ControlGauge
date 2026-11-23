"""S6-02: the /hardware routes over TestClient (conftest pattern).

The real-probe test asserts only structural truths so it holds on every
platform (the dev box HAS nvidia-smi -- three GPUs -- while CI runners
may have none or an integrated adapter): what is pinned is the closed
shape and the "nothing detected is a valid result" property, not any
particular machine. Cache-serving tests inject a sentinel snapshot and
monkeypatch detection, so they never depend on the host either.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.hardware import GPU_VENDORS, GpuInfo, HardwareInfo
from controlgauge_service.hardware.detect import cached_hardware, store_hardware_cache
from controlgauge_service.routes import hardware as hardware_routes

from .conftest import VALID_TOKEN

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}
GIB = 1024**3

OUT_FIELDS = {
    "os_name",
    "os_version",
    "cpu_model",
    "cpu_cores_logical",
    "ram_total_bytes",
    "ram_available_bytes",
    "gpus",
    "model_dir_free_bytes",
    "detection_complete",
    "detected_at",
}


def _sentinel_info() -> HardwareInfo:
    return HardwareInfo(
        os_name="linux",
        os_version="sentinel-kernel",
        cpu_model="Sentinel CPU",
        cpu_cores_logical=8,
        ram_total_bytes=32 * GIB,
        ram_available_bytes=16 * GIB,
        gpus=(GpuInfo(vendor="nvidia", name="Sentinel GPU", vram_bytes=24 * GIB),),
        model_dir_free_bytes=512 * GIB,
        detected_at=datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC),
    )


def _sentinel_payload() -> dict[str, Any]:
    """The sentinel snapshot as the route would serve it, minus
    ``detected_at`` (pydantic's exact UTC serialization is its own
    concern; the timestamp is compared as a parsed instant)."""
    info = _sentinel_info()
    return {
        "os_name": info.os_name,
        "os_version": info.os_version,
        "cpu_model": info.cpu_model,
        "cpu_cores_logical": info.cpu_cores_logical,
        "ram_total_bytes": info.ram_total_bytes,
        "ram_available_bytes": info.ram_available_bytes,
        "gpus": [
            {"vendor": gpu.vendor, "name": gpu.name, "vram_bytes": gpu.vram_bytes}
            for gpu in info.gpus
        ],
        "model_dir_free_bytes": info.model_dir_free_bytes,
        "detection_complete": info.detection_complete,
    }


def _assert_sentinel(body: dict[str, Any]) -> None:
    detected_at = body.pop("detected_at")
    assert body == _sentinel_payload()
    assert datetime.fromisoformat(detected_at) == _sentinel_info().detected_at


@pytest.fixture()
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolated data dir via the documented dev/test override; the routes
    resolve it at request time."""
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def model_dir(tmp_path: Path) -> Path:
    return tmp_path / "models"


@pytest.fixture()
def client(model_dir: Path) -> TestClient:
    """The production-mode app with an isolated model dir, so path-leak
    assertions can pin the exact string. (Built directly rather than via
    conftest's `make_client`: `make_settings` takes only its own explicit
    kwargs.)"""
    settings = Settings(
        host="127.0.0.1",
        port=51823,
        environment="production",
        session_token=VALID_TOKEN,
        model_dir=model_dir,
    )
    return TestClient(create_app(settings))


# --- auth -------------------------------------------------------------------------


def test_get_hardware_requires_auth(client: TestClient, data_dir: Path) -> None:
    assert client.get("/hardware").status_code == 401


def test_refresh_hardware_requires_auth(client: TestClient, data_dir: Path) -> None:
    assert client.post("/hardware/refresh").status_code == 401


# --- GET: cache first, detect only when cold --------------------------------------


def test_get_hardware_serves_the_cache_without_detecting(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_hardware_cache(data_dir, _sentinel_info())

    def _must_not_detect(model_dir: Path) -> HardwareInfo:
        raise AssertionError("GET must serve the cache structurally")

    monkeypatch.setattr(hardware_routes, "detect_hardware", _must_not_detect)
    response = client.get("/hardware", headers=AUTH)
    assert response.status_code == 200
    _assert_sentinel(response.json())


def test_get_hardware_cold_detects_and_stores(
    client: TestClient, data_dir: Path, model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    fresh = _sentinel_info()

    def _detect(model_dir: Path) -> HardwareInfo:
        seen.append(model_dir)
        return fresh

    monkeypatch.setattr(hardware_routes, "detect_hardware", _detect)
    response = client.get("/hardware", headers=AUTH)
    assert response.status_code == 200
    _assert_sentinel(response.json())
    assert seen == [model_dir]  # detection received the resolved model dir
    # The fresh snapshot is cached for the next structural GET.
    assert cached_hardware(data_dir) == fresh


# --- POST: always re-detect --------------------------------------------------------


def test_refresh_hardware_always_redetects_and_rewrites(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_hardware_cache(data_dir, _sentinel_info())
    stale = _sentinel_info()
    fresh = HardwareInfo(
        os_name="windows",
        os_version="11",
        cpu_model="Fresh CPU",
        cpu_cores_logical=16,
        ram_total_bytes=64 * GIB,
        ram_available_bytes=48 * GIB,
        gpus=(GpuInfo(vendor="amd", name="Fresh GPU", vram_bytes=32 * GIB),),
        model_dir_free_bytes=1024 * GIB,
        detected_at=datetime(2026, 11, 22, 8, 30, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(hardware_routes, "detect_hardware", lambda model_dir: fresh)

    response = client.post("/hardware/refresh", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["os_name"] == "windows"
    assert body["cpu_model"] == "Fresh CPU"
    # The cache now holds the fresh snapshot, not the stale one.
    assert cached_hardware(data_dir) == fresh
    assert cached_hardware(data_dir) != stale


def test_refresh_survives_a_failed_cache_write(
    client: TestClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Caching is best-effort: an unwritable data dir must not fail an
    # otherwise complete detection.
    def _unwritable(data_dir: Path, info: HardwareInfo) -> None:
        raise OSError("cache write failed")

    monkeypatch.setattr(hardware_routes, "store_hardware_cache", _unwritable)
    response = client.post("/hardware/refresh", headers=AUTH)
    assert response.status_code == 200
    assert set(response.json()) == OUT_FIELDS


def test_hardware_503_when_data_dir_unresolvable(
    make_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-absolute override is refused by storage (PD-008); the route
    # fails closed with the fixed 503 code, never the path.
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", "relative/not/absolute")
    response = make_client().post("/hardware/refresh", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "hardware_unavailable"


# --- real detection: structural truths on any platform -----------------------------


def test_get_hardware_real_detection_is_structural(
    client: TestClient, data_dir: Path, model_dir: Path
) -> None:
    """No monkeypatching: the dev box / CI runner answers from its real
    probes (all local, all bounded). Only platform-independent properties
    are asserted; the dev box's specifics are recorded as batch evidence,
    not pinned here."""
    response = client.get("/hardware", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == OUT_FIELDS
    assert body["os_name"] in {"windows", "linux", "macos", "unknown"}
    assert isinstance(body["detection_complete"], bool)
    assert isinstance(body["gpus"], list)
    for gpu in body["gpus"]:
        assert set(gpu) == {"vendor", "name", "vram_bytes"}
        assert gpu["vendor"] in GPU_VENDORS
    detected_at = datetime.fromisoformat(body["detected_at"])
    assert detected_at.tzinfo is not None
    if sys.platform in ("linux", "win32"):
        # RAM and logical cores are detectable on both supported platforms;
        # a None here would mean the probe itself is broken.
        assert body["ram_total_bytes"] is not None
        assert body["cpu_cores_logical"] is not None
    if sys.platform == "linux" and shutil.which("nvidia-smi") is None:
        # The documented Linux reality: no nvidia-smi => empty GPU list,
        # a valid result, never an error.
        assert body["gpus"] == []
        assert body["os_name"] == "linux"
    # T-10: neither the data dir nor the model dir path may appear.
    serialized = json.dumps(body)
    assert str(data_dir) not in serialized
    assert str(model_dir) not in serialized


# --- closed schemas -----------------------------------------------------------------


def test_gpu_out_vendor_is_the_closed_vocabulary() -> None:
    # Single source of truth: the route Literal must stay equal to
    # hardware.GPU_VENDORS (the Literal cannot be built from the runtime
    # tuple, so the two are pinned together here).
    assert get_args(hardware_routes.GpuOut.model_fields["vendor"].annotation) == GPU_VENDORS
    with pytest.raises(ValidationError):
        hardware_routes.GpuOut(vendor="ati", name="X", vram_bytes=None)
