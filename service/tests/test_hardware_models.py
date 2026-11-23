"""S6-02: hardware fact types, the pure parsers, and the cache contract.

Everything here is platform-independent: the Windows FFI is only exercised
through its pure helpers (``vendor_from_vendor_id``) and the Linux
subprocess through its pure parser (``parse_nvidia_smi``); the real probe
paths degrade to ``None``/empty off their home platforms and are never
asserted against real machine state (the route-level structural test
covers that honestly).
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from controlgauge_service.hardware import GpuInfo, HardwareInfo, detect_linux, detect_windows
from controlgauge_service.hardware import detect as detect_module
from controlgauge_service.hardware.detect import (
    HARDWARE_CACHE_FILENAME,
    cached_hardware,
    detect_hardware,
    store_hardware_cache,
)
from controlgauge_service.hardware.detect_linux import parse_nvidia_smi
from controlgauge_service.hardware.detect_windows import vendor_from_vendor_id

GIB = 1024**3
MIB = 1024**2


def _info(**overrides: object) -> HardwareInfo:
    """A complete, known snapshot (every scalar set) for manipulation."""
    fields: dict[str, object] = {
        "os_name": "linux",
        "os_version": "7.0.0-42-generic",
        "cpu_model": "x86_64",
        "cpu_cores_logical": 8,
        "ram_total_bytes": 32 * GIB,
        "ram_available_bytes": 16 * GIB,
        "gpus": (GpuInfo(vendor="nvidia", name="Sentinel GPU", vram_bytes=24 * GIB),),
        "model_dir_free_bytes": 512 * GIB,
        "detected_at": datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return HardwareInfo(**fields)  # type: ignore[arg-type]


# --- vendor vocabulary (pure, all platforms) ------------------------------------


@pytest.mark.parametrize(
    ("vendor_id", "expected"),
    [
        (0x10DE, "nvidia"),
        (0x1002, "amd"),
        (0x1022, "amd"),
        (0x8086, "intel"),
        (0x1414, "unknown"),
        (0, "unknown"),
    ],
)
def test_vendor_from_vendor_id_table(vendor_id: int, expected: str) -> None:
    assert vendor_from_vendor_id(vendor_id) == expected


# --- nvidia-smi CSV parsing (pure, all platforms) --------------------------------


def test_parse_nvidia_smi_single_gpu() -> None:
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 3090, 24576\n")
    expected = GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 3090", vram_bytes=24576 * MIB)
    assert gpus == (expected,)


def test_parse_nvidia_smi_multi_gpu() -> None:
    output = "GPU A, 24576\nGPU B, 24576\nGPU C, 97872\n"
    gpus = parse_nvidia_smi(output)
    assert len(gpus) == 3
    assert gpus[2] == GpuInfo(vendor="nvidia", name="GPU C", vram_bytes=97872 * MIB)


def test_parse_nvidia_smi_name_may_contain_commas() -> None:
    # Split from the right: only the LAST comma separates name from VRAM.
    gpus = parse_nvidia_smi("GeForce, RTX 4090, 24564\n")
    assert gpus == (GpuInfo(vendor="nvidia", name="GeForce, RTX 4090", vram_bytes=24564 * MIB),)


@pytest.mark.parametrize(
    "row",
    ["no-comma-at-all\n", ", 24576\n", "   , 24576\n", "Name, not-a-number\n", "Name, -5\n"],
)
def test_parse_nvidia_smi_skips_malformed_rows(row: str) -> None:
    assert parse_nvidia_smi(row) == ()


def test_parse_nvidia_smi_skips_blank_lines_and_keeps_neighbors() -> None:
    gpus = parse_nvidia_smi("\nGPU A, 24576\n\n\nGPU B, 8192\n")
    assert len(gpus) == 2


@pytest.mark.parametrize("field_text", ["N/A", "[N/A]", "n/a"])
def test_parse_nvidia_smi_na_vram_is_an_honest_unknown(field_text: str) -> None:
    # An N/A row is KEPT with vram_bytes=None: unknown, not malformed.
    gpus = parse_nvidia_smi(f"Driver Reports Nothing, {field_text}\n")
    assert gpus == (GpuInfo(vendor="nvidia", name="Driver Reports Nothing", vram_bytes=None),)


# --- HardwareInfo semantics -------------------------------------------------------


def test_hardware_info_all_unknown_fields_is_valid() -> None:
    info = HardwareInfo(
        os_name="unknown",
        os_version=None,
        cpu_model=None,
        cpu_cores_logical=None,
        ram_total_bytes=None,
        ram_available_bytes=None,
        gpus=(),
        model_dir_free_bytes=None,
        detected_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert info.detection_complete is False


def test_detection_complete_requires_every_scalar() -> None:
    base = _info()
    assert base.detection_complete is True
    for field_name in (
        "os_version",
        "cpu_model",
        "cpu_cores_logical",
        "ram_total_bytes",
        "ram_available_bytes",
        "model_dir_free_bytes",
    ):
        assert _info(**{field_name: None}).detection_complete is False, field_name


def test_detection_complete_unknown_os_is_incomplete() -> None:
    assert _info(os_name="unknown").detection_complete is False


def test_detection_complete_empty_gpus_is_not_incomplete() -> None:
    # "Nothing detected" is a valid result on Linux boxes without
    # nvidia-smi -- it must not read as an incomplete detection.
    assert _info(gpus=()).detection_complete is True


# --- detection dispatch (probe failures degrade, never raise) ---------------------


def test_detect_hardware_survives_failing_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The exploding probes must be planted on the backend detect_hardware
    # will ACTUALLY dispatch to (sys.platform): patching only the Linux
    # backend left the real Windows probes running on windows-latest and
    # the os_name assertion failing there (fix-forward 4). The
    # graceful-degradation contract below is platform-independent.
    backend = detect_windows if sys.platform == "win32" else detect_linux
    expected_os = "windows" if sys.platform == "win32" else "linux"

    def _explode() -> tuple[int, int]:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(backend, "memory", _explode)
    monkeypatch.setattr(backend, "gpus", _explode)
    monkeypatch.setattr(backend, "cpu", _explode)
    info = detect_hardware(tmp_path)
    assert info.os_name == expected_os
    assert info.ram_total_bytes is None
    assert info.ram_available_bytes is None
    assert info.gpus == ()
    assert info.cpu_model is None
    assert info.detection_complete is False


def test_detect_hardware_unknown_platform_reports_only_os_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(detect_module, "_platform_probes", lambda: ("macos", None))
    info = detect_hardware(tmp_path)
    assert info.os_name == "macos"
    assert info.os_version is None
    assert info.gpus == ()
    assert info.model_dir_free_bytes is None
    assert info.detection_complete is False


def test_disk_free_seam_is_used_for_the_model_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(detect_module, "_disk_free", lambda path: 123 * GIB)
    info = detect_hardware(tmp_path / "models")
    assert info.model_dir_free_bytes == 123 * GIB


def test_disk_free_walks_to_nearest_existing_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The model dir may not exist until the first download; the probe must
    # answer for the filesystem that will hold it -- i.e. hand disk_usage
    # the nearest EXISTING ancestor, never the missing leaf itself. The
    # patch targets the shutil module both detect.py and this file import.
    seen: list[Path] = []

    def _fake_usage(path: Path) -> SimpleNamespace:
        seen.append(Path(path))
        return SimpleNamespace(free=7 * GIB)

    monkeypatch.setattr(shutil, "disk_usage", _fake_usage)
    assert detect_module._disk_free(tmp_path / "deeply" / "nested" / "missing") == 7 * GIB
    assert seen == [tmp_path]
    assert detect_module._disk_free(tmp_path) == 7 * GIB
    assert seen == [tmp_path, tmp_path]


def test_disk_free_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _explode(path: Path) -> int:
        raise OSError("diskUsage exploded")

    monkeypatch.setattr(shutil, "disk_usage", _explode)
    assert detect_module._disk_free(tmp_path) is None


# --- cache contract -----------------------------------------------------------------


def test_cache_round_trip(tmp_path: Path) -> None:
    info = _info()
    store_hardware_cache(tmp_path, info)
    assert cached_hardware(tmp_path) == info


def test_cache_round_trip_empty_gpus(tmp_path: Path) -> None:
    info = _info(gpus=())
    store_hardware_cache(tmp_path, info)
    assert cached_hardware(tmp_path) == info


def test_cache_absent_is_none(tmp_path: Path) -> None:
    assert cached_hardware(tmp_path) is None


def test_cache_corrupt_is_deleted_and_none(tmp_path: Path) -> None:
    path = tmp_path / HARDWARE_CACHE_FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert cached_hardware(tmp_path) is None
    assert not path.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("os_name"),  # missing key
        lambda p: p.update(extra=1),  # extra key
        lambda p: p.update(os_name="freebsd"),  # os_name outside OS_NAMES
        lambda p: p.update(ram_total_bytes="32768"),  # str where int belongs
        lambda p: p.update(cpu_cores_logical=True),  # JSON bool is not an int
        lambda p: p.update(gpus=[{"vendor": "nvidia", "name": "X"}]),  # gpu key set
        # vendor outside the closed vocabulary:
        lambda p: p.update(gpus=[{"vendor": "ati", "name": "X", "vram_bytes": None}]),
        lambda p: p.update(gpus="two"),  # gpus not a list
        lambda p: p.update(detected_at="2026-11-01T12:00:00"),  # naive timestamp
        lambda p: p.update(detected_at=1761984000),  # not a string
    ],
)
def test_cache_wrong_shape_is_corrupt(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], Any]
) -> None:
    store_hardware_cache(tmp_path, _info())
    path = tmp_path / HARDWARE_CACHE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cached_hardware(tmp_path) is None
    assert not path.exists()


def test_cache_json_never_contains_the_model_dir_path(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    info = _info()
    store_hardware_cache(tmp_path, info)
    raw = (tmp_path / HARDWARE_CACHE_FILENAME).read_text(encoding="utf-8")
    assert str(model_dir) not in raw
    assert str(tmp_path) not in raw
    assert set(json.loads(raw)) == {
        "os_name",
        "os_version",
        "cpu_model",
        "cpu_cores_logical",
        "ram_total_bytes",
        "ram_available_bytes",
        "gpus",
        "model_dir_free_bytes",
        "detected_at",
    }
