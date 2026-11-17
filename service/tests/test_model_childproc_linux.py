"""Linux-only ``childproc`` properties, collected on Linux only (see
``tests/conftest.py`` -- off Linux this file does not exist for pytest at
all, so Windows suite counts are unchanged).

The POSIX die-with-parent mechanism is ``PR_SET_PDEATHSIG``, and the
kernel delivers that signal when the **spawning thread** dies -- not when
the process dies. The S5-05 route integration tests found the resulting
hole: a model child forked from a FastAPI threadpool worker was SIGKILLed
when that worker was reaped (immediately under starlette's per-request
TestClient portals; after the idle timeout under real uvicorn).
``spawn_child`` therefore forks on a persistent launcher thread; these
tests pin that property with real subprocesses.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from controlgauge_service.model_runtime import childproc


def _sleeper_script(tmp_path: Path) -> Path:
    """A child that demonstrably stays up until we terminate it."""
    script = tmp_path / "sleeper.py"
    script.write_text("import time\n\ntime.sleep(60)\n")
    return script


def _spawn(script: Path) -> childproc.ChildHandle:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return childproc.spawn_child([sys.executable, "-I", str(script)], env)


def test_child_survives_spawning_thread_death(tmp_path: Path) -> None:
    """THE S5-05 regression: the child must outlive its spawning thread.
    Before the launcher thread existed, ``handle.poll()`` returned -9
    (PDEATHSIG SIGKILL) as soon as the thread that called ``spawn_child``
    exited -- killing every route-started model mid-service."""
    holder: dict[str, childproc.ChildHandle] = {}

    def spawn_and_exit() -> None:
        holder["handle"] = _spawn(_sleeper_script(tmp_path))

    spawner = threading.Thread(target=spawn_and_exit)
    spawner.start()
    spawner.join()
    handle = holder["handle"]
    try:
        assert handle.poll() is None
        time.sleep(1.0)  # well past any request-worker reaping window
        assert handle.poll() is None, "child died with its spawning thread"
    finally:
        handle.terminate(5.0)
        handle.close()
    assert handle.poll() is not None  # terminate still owns the child


def test_spawn_failure_propagates_through_launcher(tmp_path: Path) -> None:
    """A spawn error (missing binary) crosses the launcher boundary and
    surfaces in the calling thread -- the provider's fail-closed
    ``start_failed`` mapping depends on catching it there."""
    missing = tmp_path / "does-not-exist"
    try:
        childproc.spawn_child([str(missing), "--flag"], {})
        raise AssertionError("spawn of a missing binary must raise")
    except OSError:
        pass
