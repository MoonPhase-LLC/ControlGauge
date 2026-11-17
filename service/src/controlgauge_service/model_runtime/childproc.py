"""Supervised model-server child processes (S5-02, PD-024).

Platform dispatch for spawning the ``llama-server`` subprocess so it
never outlives the service (the die-with-parent analogue of bwrap's
``--die-with-parent``, D-028 lineage):

- POSIX (Linux dev box): own process group (``process_group=0``) so
  ``terminate`` can signal the whole group, plus, on Linux, a
  ``PR_SET_PDEATHSIG`` preexec hook that makes the kernel deliver
  SIGKILL if the parent dies, with a ``getppid`` re-check closing the
  fork-to-prctl race (self-kill if the parent is already gone). The hook
  makes only async-signal-safe calls (prctl/getppid/kill); the usual
  ``preexec_fn``-with-threads caveat is accepted for exactly this narrow
  use (Sprint 5 risk register).
- Windows (``childproc_windows``): created suspended, assigned to a
  kill-on-close Job Object, then resumed -- the Job handle is held by
  this process and closed on stop, so a killed service takes the child
  with it (pattern: ``evidence/sandbox_windows.py`` minus AppContainer).

stdout/stderr are DEVNULL on every platform: llama-server logs contain
the model path, we need nothing from them (readiness is authenticated
``/health`` polling; failure diagnostics are the exit code only) -- T-10
discipline.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from typing import Any, Protocol

#: ``PR_SET_PDEATHSIG`` from <linux/prctl.h>.
_PR_SET_PDEATHSIG = 1


class _ChildControl(Protocol):
    """What the platform backends must provide (hidden from callers)."""

    def poll(self) -> int | None: ...

    def terminate(self, grace_s: float) -> None: ...

    def close(self) -> None: ...


def _pdeathsig_hook(parent_pid: int) -> Any:
    """The preexec hook, only ever installed on Linux. ``libc`` is loaded
    in the parent (never after fork); the hook itself calls prctl,
    getppid and kill only."""

    def hook() -> None:  # pragma: no cover - runs in the forked child
        if sys.platform == "win32":  # pragma: no cover - type-checking guard only
            return
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
        if os.getppid() != parent_pid:
            # The parent died between fork and prctl; the death signal
            # will never arrive. Take this process down now.
            os.kill(os.getpid(), signal.SIGKILL)

    return hook


class ChildHandle:
    """One spawned child: liveness polling and bounded, idempotent
    termination. ``process`` is the POSIX ``Popen``; on Windows the
    platform backend is carried in ``_windows`` instead (created
    suspended + Job Object; see module docs)."""

    def __init__(
        self,
        process: subprocess.Popen[bytes] | None = None,
        *,
        pid: int = 0,
        windows: _ChildControl | None = None,
    ) -> None:
        self.process = process
        self.pid = pid
        self._windows = windows

    def poll(self) -> int | None:
        """The child's exit code, or ``None`` while it is running."""
        if self._windows is not None:
            return self._windows.poll()
        if self.process is None or sys.platform == "win32":  # pragma: no cover
            return None
        return self.process.poll()

    def terminate(self, grace_s: float) -> None:
        """SIGTERM to the process group (Job termination on Windows),
        wait up to ``grace_s``, then SIGKILL/Job-kill. Idempotent; safe
        on an already-exited child."""
        if self._windows is not None:
            self._windows.terminate(grace_s)
            return
        if self.process is None or sys.platform == "win32":  # pragma: no cover
            return
        _terminate_posix(self.process, grace_s)

    def close(self) -> None:
        """Release the platform resources this handle holds (the Windows
        Job handle -- closing it kills the child via KILL_ON_JOB_CLOSE).
        Idempotent."""
        if self._windows is not None:
            self._windows.close()


def _terminate_posix(process: subprocess.Popen[bytes], grace_s: float) -> None:
    if sys.platform == "win32":  # pragma: no cover - type-checking guard only
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def spawn_child(argv: list[str], env: dict[str, str]) -> ChildHandle:
    """Spawn ``argv`` with the given (allowlisted) environment, detached
    from our process group and tied to our lifetime (module docs)."""
    if sys.platform == "win32":
        from . import childproc_windows

        child = childproc_windows.spawn(argv, env)
        return ChildHandle(windows=child, pid=child.pid)
    kwargs: dict[str, Any] = {"process_group": 0}
    if sys.platform == "linux":
        kwargs["preexec_fn"] = _pdeathsig_hook(os.getpid())
    process = subprocess.Popen(  # noqa: S603 - fixed absolute binary path from config
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        **kwargs,
    )
    return ChildHandle(process=process, pid=process.pid)
