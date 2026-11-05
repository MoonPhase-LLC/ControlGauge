"""Sprint 4 containment harness (`DECISIONS.md` D-022, PD-016).

D-022 requires that a worker which *deliberately* tries to read unrelated
evidence, open the database, reach the network, or exceed a resource bound
is observably denied or terminated. These tests run `containment_probe.py`
-- a hostile stand-in for an exploited parser -- inside the real sandbox
and assert exactly that, then run the real parser worker end to end.

Windows only: the sandbox is Windows-specific, and other platforms refuse
to parse at all (`test_non_windows_refuses_to_parse`). CI runs this file on
its Windows runner.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from controlgauge_service.evidence import limits
from controlgauge_service.evidence.ingest import interpret
from controlgauge_service.evidence.sandbox import (
    ContainedRunner,
    SandboxLimits,
    SandboxResult,
    WorkerCommand,
)

from .evidence_fixtures import docx_body, make_docx, make_pdf

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows parser sandbox")

PROBE = Path(__file__).resolve().parent / "containment_probe.py"
LIMITS = SandboxLimits(
    memory_bytes=256 * 1024 * 1024, cpu_seconds=3, wall_seconds=6, output_bytes=1024 * 1024
)


def _probe(*actions: str, limits_: SandboxLimits = LIMITS, input_path: Path) -> SandboxResult:
    from controlgauge_service.evidence import sandbox_windows

    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    command = WorkerCommand(
        base,
        ["-I", "-B", str(PROBE), *actions],
        [Path(sys.base_prefix).resolve(), PROBE.parent],
    )
    scratch_root = input_path.parent / "parser-scratch"
    scratch_root.mkdir(exist_ok=True)
    return sandbox_windows.run_contained(command, input_path, scratch_root, limits_)


def _results(result: SandboxResult) -> dict[str, str]:
    assert result.outcome == "exited", result
    assert result.exit_code == 0
    parsed: dict[str, str] = json.loads(result.output)
    return parsed


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A stand-in for the real per-user data directory: a database file,
    another artifact in the evidence store, and the file being parsed."""
    (tmp_path / "controlgauge.db").write_bytes(b"SQLite format 3\x00 sensitive")
    other = tmp_path / "evidence" / "a" / "other.bin"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another customer's evidence")
    (tmp_path / "input.bin").write_bytes(b"the one file this worker may read")
    return tmp_path


@pytest.fixture
def loopback_listener() -> Iterator[int]:
    """A live service on 127.0.0.1, like the real local API."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    accepted: list[socket.socket] = []

    def accept() -> None:
        try:
            while True:
                conn, _ = server.accept()
                accepted.append(conn)
        except OSError:
            return

    threading.Thread(target=accept, daemon=True).start()
    yield server.getsockname()[1]
    server.close()
    assert not accepted, "the sandboxed worker reached the loopback listener"


@windows_only
def test_worker_reads_only_its_own_input(data_dir: Path) -> None:
    results = _results(
        _probe(
            "stdin",
            f"read={data_dir / 'evidence' / 'a' / 'other.bin'}",
            f"list={data_dir / 'evidence'}",
            f"list={data_dir}",
            f"read={data_dir / 'controlgauge.db'}",
            f"read={data_dir / 'input.bin'}",  # by path: not granted either
            f"read={Path.home() / 'NTUSER.DAT'}",
            input_path=data_dir / "input.bin",
        )
    )
    assert results.pop("stdin") == "allowed:33"
    assert results and all(v.startswith("denied") for v in results.values()), results


@windows_only
def test_worker_cannot_write_outside_its_scratch(data_dir: Path) -> None:
    results = _probe(
        f"write={data_dir / 'evidence' / 'planted.bin'}",
        f"write={data_dir / 'controlgauge.db'}",
        f"write={Path.home() / 'planted.txt'}",
        r"write=C:\Windows\Temp\controlgauge-planted.txt",
        "write=planted-in-scratch.txt",  # the working directory is the scratch dir
        input_path=data_dir / "input.bin",
    )
    out = _results(results)
    assert out.pop("write=planted-in-scratch.txt") == "allowed"
    assert all(v.startswith("denied") for v in out.values()), out
    assert (data_dir / "controlgauge.db").read_bytes().startswith(b"SQLite format 3")
    assert not (data_dir / "evidence" / "planted.bin").exists()
    # The scratch directory is deleted after the parse.
    assert list((data_dir / "parser-scratch").iterdir()) == []


@windows_only
def test_worker_has_no_network(data_dir: Path, loopback_listener: int) -> None:
    out = _results(
        _probe(
            f"connect=127.0.0.1:{loopback_listener}",
            "connect=1.1.1.1:443",
            "connect=[::1]:445".replace("[", "").replace("]", ""),
            "resolve=example.com",
            input_path=data_dir / "input.bin",
        )
    )
    assert all(v.startswith("denied") for v in out.values()), out


@windows_only
def test_worker_environment_carries_no_secrets(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONTROLGAUGE_SESSION_TOKEN", "must-not-reach-the-worker-0123456789")
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", str(data_dir))
    out = _results(_probe("env", input_path=data_dir / "input.bin"))
    names = set(out["env"].removeprefix("allowed:").split(","))
    assert names <= {
        "LOCALAPPDATA",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
    }, names


@windows_only
def test_worker_cannot_start_processes(data_dir: Path) -> None:
    out = _results(_probe("spawn", input_path=data_dir / "input.bin"))
    assert out["spawn"].startswith("denied")


@windows_only
def test_memory_bound(data_dir: Path) -> None:
    result = _probe("alloc", input_path=data_dir / "input.bin")
    # Either the allocation fails inside the worker or the job kills it.
    if result.outcome == "exited":
        assert json.loads(result.output)["alloc"] == "denied:MemoryError"
    else:
        assert result.outcome == "memory_limit"


@windows_only
def test_cpu_bound(data_dir: Path) -> None:
    result = _probe("spin", input_path=data_dir / "input.bin")
    assert result.outcome == "cpu_limit"
    assert interpret(result).detail == "cpu_limit"


@windows_only
def test_wall_clock_bound(data_dir: Path) -> None:
    result = _probe("sleep", input_path=data_dir / "input.bin")
    assert result.outcome == "timeout"
    assert interpret(result).status == "failed"


@windows_only
def test_output_bound(data_dir: Path) -> None:
    result = _probe("flood", input_path=data_dir / "input.bin")
    assert result.outcome == "output_limit"
    assert result.output == b""


@windows_only
def test_no_sandbox_profiles_are_left_behind(data_dir: Path) -> None:
    from controlgauge_service.evidence.sandbox_windows import remove_stale_containers

    _probe("stdin", input_path=data_dir / "input.bin")
    assert remove_stale_containers() == 0


# --- the real worker, contained ----------------------------------------------------------


@windows_only
@pytest.mark.parametrize(
    ("fmt", "data", "needle"),
    [
        ("pdf", make_pdf(["Accounts are reviewed quarterly."]), "reviewed quarterly"),
        ("docx", make_docx(docx_body([("MFA is required.", None)])), "MFA is required."),
        ("txt", b"Backups run nightly.", "Backups run nightly."),
        ("csv", b"user,role\nalice,admin\n", "alice | admin"),
    ],
)
def test_real_worker_parses_inside_the_sandbox(
    tmp_path: Path, fmt: str, data: bytes, needle: str
) -> None:
    path = tmp_path / f"in.{fmt}"
    path.write_bytes(data)
    runner = ContainedRunner(tmp_path / "parser-scratch", limits.WORKER_LIMITS)
    outcome = interpret(runner.run(path, fmt))
    assert outcome.status == "parsed", outcome
    assert needle in outcome.sections[0][0]


@windows_only
def test_hostile_document_is_contained_end_to_end(tmp_path: Path) -> None:
    """A decompression bomb through the real worker: refused, bounded, and
    the service gets a fixed category back."""
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", b"<a>" + b"0" * (300 * 1024 * 1024) + b"</a>")
    path = tmp_path / "bomb.docx"
    path.write_bytes(out.getvalue())
    runner = ContainedRunner(tmp_path / "parser-scratch", limits.WORKER_LIMITS)
    outcome = interpret(runner.run(path, "docx"))
    assert (outcome.status, outcome.detail) == ("failed", "archive_limit")


@windows_only
@pytest.mark.parametrize(
    ("fmt", "data"),
    [
        ("pdf", make_pdf(["Frozen worker text."])),
        ("docx", make_docx(docx_body([("Frozen worker text.", None)]))),
        ("txt", b"Frozen worker text."),
        ("csv", b"a\nFrozen worker text.\n"),
    ],
)
def test_frozen_worker_parses_inside_the_sandbox(
    pytestconfig: pytest.Config, tmp_path: Path, fmt: str, data: bytes
) -> None:
    """With `--service-exe`, the packaged executable's `--parse-worker`
    mode runs contained (its bundle directory is the only runtime grant)."""
    exe_option = pytestconfig.getoption("--service-exe")
    if not exe_option:
        pytest.skip("pass --service-exe to test the frozen worker")
    from controlgauge_service.evidence import sandbox_windows

    exe = Path(str(exe_option)).resolve()
    path = tmp_path / f"in.{fmt}"
    path.write_bytes(data)
    scratch = tmp_path / "parser-scratch"
    scratch.mkdir()
    command = WorkerCommand(exe, ["--parse-worker", fmt], [exe.parent])
    outcome = interpret(sandbox_windows.run_contained(command, path, scratch, limits.WORKER_LIMITS))
    assert outcome.status == "parsed", outcome
    assert "Frozen worker text." in outcome.sections[0][0]


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behavior")
def test_non_windows_refuses_to_parse(tmp_path: Path) -> None:
    path = tmp_path / "in.txt"
    path.write_bytes(b"text")
    result = ContainedRunner(tmp_path, limits.WORKER_LIMITS).run(path, "txt")
    assert result.outcome == "containment_unavailable"
    assert interpret(result).detail == "containment_unavailable"


def test_probe_is_not_shipped() -> None:
    """The probe lives under tests/, which the packaging script refuses to
    bundle (`scripts/package-windows.ps1` forbidden list)."""
    assert PROBE.parent.name == "tests"
    assert os.path.basename(PROBE) == "containment_probe.py"
