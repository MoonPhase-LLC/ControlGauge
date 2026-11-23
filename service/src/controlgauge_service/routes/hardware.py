"""Local hardware detection routes (Sprint 6, S6-02).

    GET  /hardware           last detected hardware (structural)
    POST /hardware/refresh   explicit re-detect (USER_FLOWS §2.4)

Local-only by construction: both routes answer from the ``hardware.json``
cache or one bounded local probe pass (``hardware/detect.py``) -- they
never contact a model child and never touch the network. Responses carry
machine facts only: no model-dir path, no data-dir path, no file name
(T-10; the disk figure is the free bytes *of* the model directory, never
the path itself).

``GET`` is the structural route of the pair: the cached snapshot when one
exists, one fresh (cached best-effort) detection otherwise. ``POST``
always re-detects -- the explicit re-detect of USER_FLOWS §2.4. A data-dir
resolution failure is 503 fail-closed (D-009 pattern); a failed cache
write is only a warning, because the fresh answer is still complete.

All handlers are plain ``def``: FastAPI runs them in its worker threadpool
(``routes/models.py`` pattern), where the bounded multi-second probes
belong.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.requests import Request

from ..config import Settings, resolve_model_dir
from ..hardware.detect import cached_hardware, detect_hardware, store_hardware_cache
from ..hardware.models import HardwareInfo
from ..storage import StorageLocationError, ensure_data_dir, resolve_data_dir
from .errors import ApiError

logger = logging.getLogger("controlgauge_service.routes.hardware")

router = APIRouter()


# --- response schemas (closed) -------------------------------------------------


class GpuOut(BaseModel):
    """One detected GPU. ``vendor`` is the closed ``GPU_VENDORS``
    vocabulary (detection never guesses a vendor outside it; the Literal
    members are pinned equal to ``hardware.GPU_VENDORS`` by test);
    ``name`` and ``vram_bytes`` are ``None`` when unknown."""

    vendor: Literal["nvidia", "amd", "intel", "unknown"]
    name: str | None
    vram_bytes: int | None


class HardwareInfoOut(BaseModel):
    """Closed machine-facts snapshot. No path, URL, or credential field
    exists here (T-10); ``detection_complete`` is false when any expected
    scalar field is unknown -- an empty ``gpus`` list is a valid result,
    not a failure."""

    os_name: str
    os_version: str | None
    cpu_model: str | None
    cpu_cores_logical: int | None
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    gpus: list[GpuOut]
    model_dir_free_bytes: int | None
    detection_complete: bool
    detected_at: datetime


# --- dependencies ----------------------------------------------------------------


def get_settings(request: Request) -> Settings:
    """The app's resolved settings (``create_app`` always stores them);
    absent is fail-closed like every other route dependency."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover - create_app always sets it
        raise ApiError(503, "hardware_unavailable", "Hardware detection is not available.")
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- helpers ----------------------------------------------------------------------


def _resolve_data_dir() -> Path:
    """The per-user data dir, created if needed (the cache lives beside
    the database). Failure is fail-closed: fixed code and message, never
    the path (PD-008 / T-10)."""
    try:
        return ensure_data_dir(resolve_data_dir())
    except StorageLocationError:
        raise ApiError(
            503, "hardware_unavailable", "Hardware detection is not available."
        ) from None


def _detect_and_store(data_dir: Path, model_dir: Path) -> HardwareInfoOut:
    info = detect_hardware(model_dir)
    try:
        store_hardware_cache(data_dir, info)
    except OSError:
        # Caching is best-effort: an unwritable data dir must not fail an
        # otherwise complete detection (the fresh answer below still holds).
        logger.warning("hardware_cache_store_failed")
    return _to_out(info)


def _to_out(info: HardwareInfo) -> HardwareInfoOut:
    gpus = [
        GpuOut(vendor=gpu.vendor, name=gpu.name, vram_bytes=gpu.vram_bytes) for gpu in info.gpus
    ]
    return HardwareInfoOut(
        os_name=info.os_name,
        os_version=info.os_version,
        cpu_model=info.cpu_model,
        cpu_cores_logical=info.cpu_cores_logical,
        ram_total_bytes=info.ram_total_bytes,
        ram_available_bytes=info.ram_available_bytes,
        gpus=gpus,
        model_dir_free_bytes=info.model_dir_free_bytes,
        detection_complete=info.detection_complete,
        detected_at=info.detected_at,
    )


# --- routes ------------------------------------------------------------------------


@router.get("/hardware")
def get_hardware(settings: SettingsDep) -> HardwareInfoOut:
    """The cached detection when present, else one fresh detection (cached
    best-effort). Structural with respect to the model runtime: never a
    live check, never a model child."""
    data_dir = _resolve_data_dir()
    cached = cached_hardware(data_dir)
    if cached is not None:
        return _to_out(cached)
    return _detect_and_store(data_dir, resolve_model_dir(settings))


@router.post("/hardware/refresh")
def refresh_hardware(settings: SettingsDep) -> HardwareInfoOut:
    """Always re-detect and rewrite the cache -- the explicit re-detect
    (USER_FLOWS §2.4); staleness is never fixed by re-GETting."""
    data_dir = _resolve_data_dir()
    return _detect_and_store(data_dir, resolve_model_dir(settings))
