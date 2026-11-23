"""Linux hardware probes (S6-02).

Local-only and best-effort: every probe returns ``None``/empty on any
failure, never an error (``docs/MODEL_RUNTIME.md`` §4). Mechanisms are
stdlib only: ``/proc/meminfo`` for RAM, ``nvidia-smi`` (bounded) for GPUs
when present -- absent means an empty GPU list, which is a *valid result*
(not "zero GPUs exist": AMD/Intel are not probed on Linux in V0.1).

``parse_nvidia_smi`` and the ``/proc/meminfo`` parser are pure functions so
they are unit-tested with synthetic input on any platform; the subprocess
call is isolated in ``gpus()``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

from .models import GpuInfo

#: Bounded so a wedged driver cannot stall ``GET /hardware`` (the Rust
#: deadline is sized for this cap plus margin).
NVIDIA_SMI_TIMEOUT_S = 5.0

#: The fixed query: one CSV row per GPU, ``name,memory.total`` in MiB.
_NVIDIA_SMI_ARGS = (
    "--query-gpu=name,memory.total",
    "--format=csv,noheader,nounits",
)

_KIB = 1024
_MIB = 1024 * 1024


def memory() -> tuple[int | None, int | None]:
    """Total/available RAM in bytes from ``/proc/meminfo`` (KiB fields).
    Any read/parse failure degrades to ``(None, None)``."""
    try:
        text = _read_meminfo()
    except (OSError, ValueError):
        return (None, None)
    return _parse_meminfo(text)


def _read_meminfo() -> str:
    with open("/proc/meminfo", encoding="ascii") as handle:
        return handle.read()


def _parse_meminfo(text: str) -> tuple[int | None, int | None]:
    """``MemTotal:``/``MemAvailable:`` KiB -> bytes. A missing field stays
    ``None`` (``MemAvailable`` is kernel >= 3.14; a missing field is an
    honest unknown, not an error)."""
    total: int | None = None
    available: int | None = None
    for line in text.splitlines():
        label, _, rest = line.partition(":")
        if label == "MemTotal":
            total = _kib_field(rest)
        elif label == "MemAvailable":
            available = _kib_field(rest)
        if total is not None and available is not None:
            break
    if total is not None:
        total *= _KIB
    if available is not None:
        available *= _KIB
    return (total, available)


def _kib_field(rest: str) -> int | None:
    """The integer KiB value of one meminfo value field (``"  16384 kB"``);
    ``None`` unless the field is exactly a non-negative integer."""
    fields = rest.split()
    if len(fields) != 2 or fields[1] != "kB":
        return None
    try:
        kib = int(fields[0])
    except ValueError:
        return None
    return kib if kib >= 0 else None


def gpus() -> tuple[GpuInfo, ...]:
    """One ``GpuInfo`` per ``nvidia-smi`` row, or ``()`` when the tool is
    absent or the probe fails for any reason (bounded by
    ``NVIDIA_SMI_TIMEOUT_S``)."""
    resolved = shutil.which("nvidia-smi")
    if resolved is None:
        return ()
    try:
        output = subprocess.run(  # noqa: S603 - fixed argv (resolved above), no shell, no user input
            [resolved, *_NVIDIA_SMI_ARGS],
            capture_output=True,
            check=True,
            timeout=NVIDIA_SMI_TIMEOUT_S,
            encoding="utf-8",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    return parse_nvidia_smi(output)


def parse_nvidia_smi(output: str) -> tuple[GpuInfo, ...]:
    """Parse ``--format=csv,noheader,nounits`` rows (``name, <MiB>``).

    Pure: synthetic CSV is unit-tested on any platform. A GPU name may
    itself contain a comma, so the split is from the right. A malformed
    row is skipped; an ``N/A``/``[N/A]`` VRAM field is an honest unknown
    (``None``); a non-numeric, non-N/A VRAM field makes the whole row
    malformed (a VRAM we cannot interpret must not silently become 0).
    """
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.rsplit(",", 1)
        if len(parts) != 2:
            continue  # malformed row: skipped, never guessed
        name = parts[0].strip()
        vram_text = parts[1].strip()
        if not name:
            continue
        vram_unknown = vram_text.upper().strip("[]").strip() == "N/A"
        vram_bytes = None if vram_unknown else _vram_to_bytes(vram_text)
        if vram_bytes is None and not vram_unknown:
            continue  # malformed row: skipped, never guessed
        gpus.append(GpuInfo(vendor="nvidia", name=name, vram_bytes=vram_bytes))
    return tuple(gpus)


def _vram_to_bytes(field_text: str) -> int | None:
    """``memory.total`` in MiB -> bytes. ``N/A``/``[N/A]`` -> ``None``;
    anything unparseable or negative -> ``None`` as well (callers that
    need to distinguish "unknown" from "malformed row" re-check the raw
    field, see ``parse_nvidia_smi``)."""
    cleaned = field_text.strip().strip("[]").strip()
    if cleaned.upper() == "N/A":
        return None
    try:
        mib = int(cleaned)
    except ValueError:
        return None
    if mib < 0:
        return None
    return mib * _MIB


def cpu() -> tuple[str | None, int | None]:
    """``platform.processor()`` (empty -> unknown) and ``os.cpu_count()``.
    On Linux this is typically the architecture string rather than a
    marketing model name -- reported as detected, never embellished."""
    model = platform.processor() or None
    return (model, os.cpu_count())
