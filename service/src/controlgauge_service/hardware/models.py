"""Hardware fact types (S6-02).

Every field is best-effort: ``None``/empty means "not detected on this
machine", never an error (``docs/MODEL_RUNTIME.md`` §4). An empty GPU list
means *nothing was detected* -- on Linux that is the honest result on a
box without ``nvidia-smi``, and recommendations must treat it as CPU-only
(never as "zero GPUs exist"). These types are plain dataclasses, not
pydantic models: detection is a service-internal concern and the route
layer owns the wire schema (``routes/hardware.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: The closed vendor vocabulary. Detection never guesses: a vendor that is
#: not one of the known PCI vendor IDs maps to ``"unknown"``.
GPU_VENDORS = ("nvidia", "amd", "intel", "unknown")

#: ``HardwareInfo.os_name`` values. Anything not in this mapping (and not
#: one of the detected platforms) is ``"unknown"``.
OS_NAMES = ("windows", "linux", "macos", "unknown")


@dataclass(frozen=True)
class GpuInfo:
    """One detected GPU (one DXGI adapter on Windows, one ``nvidia-smi``
    row on Linux). ``name``/``vram_bytes`` are ``None`` when the probe
    could not determine them -- never guessed, never defaulted to 0."""

    vendor: str  # member of GPU_VENDORS
    name: str | None  # None = unknown; never guessed
    vram_bytes: int | None  # None = unknown


@dataclass(frozen=True)
class HardwareInfo:
    """The full best-effort hardware snapshot.

    ``detected_at`` is the snapshot time (UTC), not a freshness claim:
    ``GET /hardware`` serves whatever the cache holds, however old; the
    explicit ``POST /hardware/refresh`` re-detects (USER_FLOWS §2.4).
    """

    os_name: str  # member of OS_NAMES
    os_version: str | None
    cpu_model: str | None
    cpu_cores_logical: int | None
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    gpus: tuple[GpuInfo, ...]  # empty = none detected (NOT an error)
    model_dir_free_bytes: int | None  # stat of the model dir only -- never the path itself
    detected_at: datetime  # UTC
    #: ``detection_complete`` is derived, never stored: a flag field could
    #: drift from the fields it summarizes (and the strict cache loader
    #: would then have to trust a stale flag). It is recomputed on every
    #: access, including after a cache round-trip.

    @property
    def detection_complete(self) -> bool:
        """False when any expected scalar field is unknown. An empty GPU
        list does NOT make detection incomplete (nothing detected is a
        valid result); a *detected* GPU's own fields are covered by the
        GPU-path verdicts, not by this flag."""
        return (
            self.os_name != "unknown"
            and self.os_version is not None
            and self.cpu_model is not None
            and self.cpu_cores_logical is not None
            and self.ram_total_bytes is not None
            and self.ram_available_bytes is not None
            and self.model_dir_free_bytes is not None
        )


__all__ = ["GPU_VENDORS", "OS_NAMES", "GpuInfo", "HardwareInfo"]
