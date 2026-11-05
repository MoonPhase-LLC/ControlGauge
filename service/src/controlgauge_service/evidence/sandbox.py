"""Platform-neutral interface to the parser containment boundary (D-022,
PD-016).

`ContainedRunner` is the production runner: on Windows it runs each parse
in `sandbox_windows.run_contained` (LPAC AppContainer + Job Object). On
any other platform it refuses to parse (`containment_unavailable`) rather
than run an unconfined parser -- there is no silent fallback.

Tests on platforms without the sandbox inject `UnconfinedTestRunner`
explicitly; it is never selected by configuration or environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

SandboxOutcome = Literal[
    "exited",
    "timeout",
    "cpu_limit",
    "memory_limit",
    "output_limit",
    "launch_failed",
    "containment_unavailable",
]


@dataclass(frozen=True)
class SandboxLimits:
    memory_bytes: int
    cpu_seconds: float
    wall_seconds: float
    output_bytes: int


@dataclass(frozen=True)
class WorkerCommand:
    """The program to run and the directories it must be able to read
    (its interpreter/bundle and code; never data)."""

    executable: Path
    args: list[str]
    runtime_dirs: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class SandboxResult:
    outcome: SandboxOutcome
    exit_code: int | None
    output: bytes


class ParseRunner(Protocol):
    def run(self, input_path: Path, fmt: str) -> SandboxResult: ...


def _site_dirs() -> list[Path]:
    """Directories holding this package and its runtime dependencies."""
    import pypdf

    from .. import evidence

    dirs = {
        Path(evidence.__file__).resolve().parent.parent.parent,  # dir holding the package
        Path(pypdf.__file__).resolve().parent.parent,
    }
    return sorted(dirs)


def worker_command(fmt: str) -> WorkerCommand:
    """How to start the parser worker for this installation.

    - Frozen (PyInstaller --onedir): the service executable itself, in its
      `--parse-worker` mode; the bundle directory is the runtime.
    - Development: the base interpreter (a venv `python.exe` is a launcher
      that spawns a second process, which the one-process job limit
      forbids), isolated mode, with this package's directories put on
      `sys.path` by a fixed bootstrap.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return WorkerCommand(exe, ["--parse-worker", fmt], [exe.parent])
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    sites = _site_dirs()
    bootstrap = (
        "import sys;"
        f"sys.path[0:0]={[str(p) for p in sites]!r};"
        "from controlgauge_service.evidence.worker import main;"
        "main()"
    )
    return WorkerCommand(
        base,
        ["-I", "-B", "-c", bootstrap, fmt],
        [Path(sys.base_prefix).resolve(), *sites],
    )


class ContainedRunner:
    """Production runner. See module docs."""

    def __init__(self, scratch_root: Path, limits: SandboxLimits) -> None:
        self._scratch_root = scratch_root
        self._limits = limits

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def run(self, input_path: Path, fmt: str) -> SandboxResult:
        if sys.platform != "win32":
            return SandboxResult("containment_unavailable", None, b"")
        from . import sandbox_windows

        self._scratch_root.mkdir(parents=True, exist_ok=True)
        return sandbox_windows.run_contained(
            worker_command(fmt), input_path, self._scratch_root, self._limits
        )


class UnconfinedTestRunner:
    """TEST ONLY. Runs the same worker as a plain subprocess with the same
    wall-clock and output bounds but **no containment**. Used by tests on
    platforms where the sandbox does not exist, injected explicitly."""

    def __init__(self, limits: SandboxLimits) -> None:
        self._limits = limits

    def run(self, input_path: Path, fmt: str) -> SandboxResult:
        cmd = [sys.executable, "-I", "-B", "-c", _unconfined_bootstrap(), fmt]
        with open(input_path, "rb") as stdin:
            try:
                proc = subprocess.run(  # noqa: S603 - fixed argv, test-only
                    cmd,
                    stdin=stdin,
                    capture_output=True,
                    timeout=self._limits.wall_seconds,
                    env={"PYTHONUTF8": "1", "SystemRoot": os.environ.get("SystemRoot", "")},
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult("timeout", None, b"")
        if len(proc.stdout) > self._limits.output_bytes:
            return SandboxResult("output_limit", proc.returncode, b"")
        return SandboxResult("exited", proc.returncode, proc.stdout)


def _unconfined_bootstrap() -> str:
    sites = [str(p) for p in _site_dirs()]
    return (
        f"import sys;sys.path[0:0]={sites!r};"
        "from controlgauge_service.evidence.worker import main;main()"
    )
