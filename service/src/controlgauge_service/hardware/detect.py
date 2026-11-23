"""Hardware detection dispatch and cache (S6-02).

``detect_hardware`` dispatches per ``sys.platform`` and wraps every probe:
a failure anywhere yields ``None``/empty for that field, never an error
(``docs/MODEL_RUNTIME.md`` §4). macOS/other platforms report only the
mapped OS name in V0.1 -- every probe is honestly unknown there.

The snapshot is cached in the data dir (``hardware.json``, machine facts
deliberately not in the assessment database): ``GET /hardware`` serves the
cache structurally when present, the explicit ``POST /hardware/refresh``
always re-detects and rewrites it. The cache loader is strict -- exact key
set, exact types -- and a corrupt file is deleted and treated as absent
(stale-but-valid caches are served; stale-but-*unreadable* ones are not).
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NamedTuple, TypeVar

from . import detect_linux, detect_windows
from .detect_linux import NVIDIA_SMI_TIMEOUT_S
from .models import GPU_VENDORS, OS_NAMES, GpuInfo, HardwareInfo

__all__ = [
    "HARDWARE_CACHE_FILENAME",
    "NVIDIA_SMI_TIMEOUT_S",
    "cached_hardware",
    "detect_hardware",
    "store_hardware_cache",
]

#: The cache file lives directly in the data dir (beside the database).
HARDWARE_CACHE_FILENAME = "hardware.json"

#: The typed "unknown" fallbacks: a bare ``(None, None)`` literal would
#: bind the probe/fallback TypeVar to ``tuple[None, None]``.
_UNKNOWN_MEMORY: Final[tuple[int | None, int | None]] = (None, None)
_UNKNOWN_CPU: Final[tuple[str | None, int | None]] = (None, None)

_T = TypeVar("_T")


class _Probes(NamedTuple):
    """The per-platform probe callables ``detect_hardware`` dispatches to."""

    memory: Callable[[], tuple[int | None, int | None]]
    gpus: Callable[[], tuple[GpuInfo, ...]]
    cpu: Callable[[], tuple[str | None, int | None]]


def _platform_probes() -> tuple[str, _Probes | None]:
    """``(os_name, probes)`` for this machine; ``probes`` is ``None`` on
    platforms V0.1 does not detect on (macOS/other)."""
    if sys.platform == "win32":
        return ("windows", _Probes(detect_windows.memory, detect_windows.gpus, detect_windows.cpu))
    if sys.platform == "linux":
        return ("linux", _Probes(detect_linux.memory, detect_linux.gpus, detect_linux.cpu))
    if sys.platform == "darwin":
        return ("macos", None)
    return ("unknown", None)


def detect_hardware(model_dir: Path) -> HardwareInfo:
    """One best-effort snapshot. ``model_dir`` is used only for the disk
    probe (its free bytes) and never recorded itself (T-10)."""
    os_name, probes = _platform_probes()
    detected_at = _utc_now()
    if probes is None:
        return HardwareInfo(
            os_name=os_name,
            os_version=None,
            cpu_model=None,
            cpu_cores_logical=None,
            ram_total_bytes=None,
            ram_available_bytes=None,
            gpus=(),
            model_dir_free_bytes=None,
            detected_at=detected_at,
        )
    ram_total, ram_available = _wrapped(probes.memory, _UNKNOWN_MEMORY)
    cpu_model, cpu_cores = _wrapped(probes.cpu, _UNKNOWN_CPU)
    return HardwareInfo(
        os_name=os_name,
        os_version=_os_version(),
        cpu_model=cpu_model,
        cpu_cores_logical=cpu_cores,
        ram_total_bytes=ram_total,
        ram_available_bytes=ram_available,
        gpus=_wrapped(probes.gpus, ()),
        model_dir_free_bytes=_disk_free(model_dir),
        detected_at=detected_at,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _os_version() -> str | None:
    """The short OS release string (kernel release on Linux, ``10``/``11``
    on Windows); empty or failed -> unknown."""
    try:
        return platform.release() or None
    except Exception:  # noqa: BLE001 - probe failure degrades to unknown
        return None


def _wrapped(probe: Callable[[], _T], fallback: _T) -> _T:
    """Run one probe; any surprise degrades to the fallback. Belt and
    braces around the platform modules' own wrapping: a probe can never
    break the "detection never raises" contract."""
    try:
        return probe()
    except Exception:  # noqa: BLE001 - probe failure degrades to unknown
        return fallback


def _disk_free(path: Path) -> int | None:
    """Free bytes on the filesystem that holds (or would hold) ``path`` --
    the model directory may not exist until the first download, so the
    nearest existing ancestor is probed. The path itself never leaves this
    function (T-10). Seam: tests monkeypatch this to inject sizes without
    allocating anything (no test fixture may allocate a huge file)."""
    try:
        probe = path
        while not probe.exists():
            parent = probe.parent
            if parent == probe:  # reached the filesystem root
                return None
            probe = parent
        return int(shutil.disk_usage(probe).free)
    except (OSError, ValueError):
        return None


# --- cache ----------------------------------------------------------------------


#: The exact cache schema (``HardwareInfo`` minus the derived
#: ``detection_complete``, which is recomputed on load so a stale flag can
#: never be served).
_CACHE_KEYS = frozenset(
    {
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
)
_GPU_KEYS = frozenset({"vendor", "name", "vram_bytes"})


def store_hardware_cache(data_dir: Path, info: HardwareInfo) -> None:
    """Rewrite ``<data_dir>/hardware.json``. ``OSError`` propagates:
    caching is best-effort and the caller decides whether that may fail
    the request (the routes log a warning and keep the fresh answer)."""
    payload = {
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
        "detected_at": info.detected_at.isoformat(),
    }
    (data_dir / HARDWARE_CACHE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def cached_hardware(data_dir: Path) -> HardwareInfo | None:
    """The cached snapshot, or ``None`` when absent -- or corrupt, in
    which case the file is deleted so it cannot wedge future refreshes."""
    path = data_dir / HARDWARE_CACHE_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    info = _parse_cache(raw)
    if info is None:
        _discard_cache(path)
    return info


def _discard_cache(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _parse_cache(raw: str) -> HardwareInfo | None:
    """Strict load: exact key set, exact types, closed vocabularies, and a
    timezone-aware ``detected_at``. Anything else is corrupt."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != set(_CACHE_KEYS):
        return None
    if not (
        isinstance(payload["os_name"], str)
        and payload["os_name"] in OS_NAMES
        and _is_optional_str(payload["os_version"])
        and _is_optional_str(payload["cpu_model"])
        and _is_optional_int(payload["cpu_cores_logical"])
        and _is_optional_int(payload["ram_total_bytes"])
        and _is_optional_int(payload["ram_available_bytes"])
        and _is_optional_int(payload["model_dir_free_bytes"])
    ):
        return None
    gpus = _parse_cached_gpus(payload["gpus"])
    if gpus is None:
        return None
    detected_at = _parse_utc(payload["detected_at"])
    if detected_at is None:
        return None
    return HardwareInfo(
        os_name=payload["os_name"],
        os_version=payload["os_version"],
        cpu_model=payload["cpu_model"],
        cpu_cores_logical=payload["cpu_cores_logical"],
        ram_total_bytes=payload["ram_total_bytes"],
        ram_available_bytes=payload["ram_available_bytes"],
        gpus=gpus,
        model_dir_free_bytes=payload["model_dir_free_bytes"],
        detected_at=detected_at,
    )


def _parse_cached_gpus(raw: object) -> tuple[GpuInfo, ...] | None:
    if not isinstance(raw, list):
        return None
    gpus: list[GpuInfo] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != set(_GPU_KEYS):
            return None
        vendor = entry["vendor"]
        if not isinstance(vendor, str) or vendor not in GPU_VENDORS:
            return None
        if not _is_optional_str(entry["name"]) or not _is_optional_int(entry["vram_bytes"]):
            return None
        gpus.append(GpuInfo(vendor=vendor, name=entry["name"], vram_bytes=entry["vram_bytes"]))
    return tuple(gpus)


def _parse_utc(raw: object) -> datetime | None:
    """A timezone-aware ISO 8601 timestamp (the writer always emits UTC);
    naive timestamps are corrupt, not naive-but-served."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_optional_str(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_optional_int(value: object) -> bool:
    # JSON booleans are Python ints: an explicit exclusion, not pedantry.
    return value is None or (isinstance(value, int) and not isinstance(value, bool))
