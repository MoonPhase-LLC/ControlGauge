"""Windows Job-Object backend for the model-server child, REAL children.

Collected only on Windows (conftest ``collect_ignore_glob`` mirrors
``test_model_childproc_linux.py``); these tests execute on the
windows-latest CI job and pin the backend's observable contract --
liveness must stay truthful across the terminate/close lifecycle the
provider drives.
"""

from __future__ import annotations

import os
import sys

from controlgauge_service.model_runtime import childproc_windows


def _child_env() -> dict[str, str]:
    """A minimal healthy-Windows-child environment (args.py allowlist
    rationale: ``SystemRoot`` is what a Windows child cannot start
    without)."""
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    return env


def test_poll_reports_exit_code_after_terminate_and_close() -> None:
    """Regression (windows-latest, found by test_stop_terminates_child):
    close() used to release the process handle, after which poll() hit
    an invalid handle and answered None -- a STOPPED child read as
    "still running" forever. provider.stop() terminates then closes, so
    the stopped state must stay observable afterwards."""
    child = childproc_windows.spawn([sys.executable, "-c", "import sys; sys.exit(3)"], _child_env())
    child.terminate(2.0)
    child.close()

    assert child.poll() is not None  # never reads as "running" post-close


def test_terminate_kills_a_live_child_within_the_grace() -> None:
    child = childproc_windows.spawn(
        [sys.executable, "-c", "import time; time.sleep(120)"], _child_env()
    )
    try:
        child.terminate(2.0)
        assert child.poll() is not None
    finally:
        child.close()
    # Still truthful after the handles are gone (idempotent close).
    assert child.poll() is not None
