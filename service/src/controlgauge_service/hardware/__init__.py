"""Local-only, best-effort hardware detection (S6-02).

Every probe is local (no network, no external tools beyond the
platform's own interfaces); every failure degrades to ``None``/empty for
that field -- never an error, never a partial route failure. The detected
set is cached in the data dir (``hardware.json``) and served structurally
by ``GET /hardware``; the explicit ``POST /hardware/refresh`` re-detects.
Machine facts deliberately live here, not in the assessment database
(``docs/DATABASE.md`` §11, ``docs/MODEL_RUNTIME.md`` §4).
"""

from .models import GPU_VENDORS, GpuInfo, HardwareInfo

__all__ = ["GPU_VENDORS", "GpuInfo", "HardwareInfo"]
