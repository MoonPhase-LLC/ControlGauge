"""Linux-specific containment properties (`DECISIONS.md` D-028), complementing
the shared harness in `test_containment.py`: the fail-closed paths around the
bubblewrap availability probe, plus the host-side cleanliness and scratch-bound
properties. Collected on Linux only (see `tests/conftest.py`); on other
platforms this file does not exist for pytest at all, so it adds no skips
anywhere else.

Paths and probe output are never logged (T-10 discipline).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from controlgauge_service.evidence import limits
from controlgauge_service.evidence.ingest import interpret
from controlgauge_service.evidence.sandbox import ContainedRunner, WorkerCommand

from .test_containment import LIMITS, PROBE


def _linux_command(*actions: str) -> WorkerCommand:
    """The same `WorkerCommand` shape the shared harness probes with: the
    base interpreter running the hostile probe file, with the tests
    directory ro-bound so the probe is readable inside the sandbox."""
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    return WorkerCommand(
        base,
        ["-I", "-B", str(PROBE), *actions],
        [Path(sys.base_prefix).resolve(), PROBE.parent],
    )


def test_bwrap_missing_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from controlgauge_service.evidence import sandbox_linux

    monkeypatch.setattr(sandbox_linux, "_BWRAP_CANDIDATES", ())
    # sandbox_linux resolves bwrap through the same shutil module object.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    sandbox_linux.reset_probe_cache()
    try:
        path = tmp_path / "in.txt"
        path.write_bytes(b"text")
        runner = ContainedRunner(tmp_path / "parser-scratch", limits.WORKER_LIMITS)
        assert not ContainedRunner.available()
        result = runner.run(path, "txt")
    finally:
        sandbox_linux.reset_probe_cache()  # don't poison other tests
    assert result.outcome == "containment_unavailable"
    assert interpret(result).detail == "containment_unavailable"


def test_probe_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from controlgauge_service.evidence import sandbox_linux

    calls: list[int] = []
    real_probe = sandbox_linux._probe_once

    def counting_probe() -> bool:
        calls.append(1)
        return real_probe()

    monkeypatch.setattr(sandbox_linux, "_probe_once", counting_probe)
    sandbox_linux.reset_probe_cache()
    try:
        first = ContainedRunner.available()
        second = ContainedRunner.available()
    finally:
        sandbox_linux.reset_probe_cache()  # don't poison other tests
    assert calls == [1]  # one real probe; the second call used the cache
    assert first is second is True


def test_no_sandbox_state_is_left_behind(tmp_path: Path) -> None:
    """Linux counterpart of the Windows registry-profile test: the per-parse
    scratch is a tmpfs inside an ephemeral mount namespace and bwrap
    registers no host-side state, so a contained run leaves the scratch
    root on the host empty."""
    from controlgauge_service.evidence import sandbox_linux

    input_path = tmp_path / "in.txt"
    input_path.write_bytes(b"text")
    scratch_root = tmp_path / "parser-scratch"
    scratch_root.mkdir()
    result = sandbox_linux.run_contained(_linux_command("stdin"), input_path, scratch_root, LIMITS)
    assert result.outcome == "exited", result
    assert json.loads(result.output)["stdin"] == "allowed:4"
    assert list(scratch_root.iterdir()) == []


def test_scratch_size_is_bounded(tmp_path: Path) -> None:
    """The only writable location is itself size-bounded: flooding the tmpfs
    scratch fails with ENOSPC instead of consuming unbounded memory or ever
    reaching the host disk."""
    from controlgauge_service.evidence import sandbox_linux

    input_path = tmp_path / "in.txt"
    input_path.write_bytes(b"text")
    scratch_root = tmp_path / "parser-scratch"
    scratch_root.mkdir()
    result = sandbox_linux.run_contained(
        _linux_command("floodscratch"), input_path, scratch_root, LIMITS
    )
    assert result.outcome == "exited", result
    out = json.loads(result.output)
    assert out["floodscratch"].startswith("denied"), out
    assert list(scratch_root.iterdir()) == []
