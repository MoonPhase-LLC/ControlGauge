"""The parser containment boundary on Linux (Sprint 4.5, `DECISIONS.md` D-028;
implements D-022 with the same PD-016 limits as the Windows boundary, D-027).

Each parse runs in a fresh bubblewrap sandbox that is:

- **Namespaced away from the host.** `--unshare-all` gives the worker its own
  pid, network, user, ipc, uts, cgroup and mount namespaces. The network
  namespace has no interfaces (loopback exists but is down, unconfigured), and
  no resolver configuration is visible, so sockets cannot connect and DNS
  cannot resolve. `/proc` inside shows only the sandbox's own processes.
- **Given only what parsing needs.** Read-only binds expose the worker's
  program directories (its interpreter/bundle, the package sources,
  site-packages) plus the system code trees `/usr`, `/lib`, `/lib64` -- the
  Linux analogue of the Windows boundary's system-DLL read grant. The data
  directory, evidence store, database, home directory and `/etc` are **not
  bound at all**: those paths do not exist inside the sandbox (ENOENT), so
  denial is enforced by the mount namespace, not by convention. `/etc` is
  deliberately excluded (no resolv.conf, passwd, or CA material).
- **Fed its input by read-only handle.** The trusted service opens the stored
  file O_RDONLY and passes the fd as the sandbox's stdin; the worker never
  receives a path to the evidence (bwrap `--file`/`--ro-bind` of the input
  were rejected: both would hand the worker a path).
- **Write-confined to a per-parse tmpfs.** The only writable location is
  `/scratch`, a 128 MiB, mode-0700 tmpfs that exists only inside the mount
  namespace: worker writes never touch the disk and vanish with the sandbox.
  bwrap creates mountpoint directories for the read-only binds (e.g. `/home`,
  `/home/<user>` when a runtime directory lives under `$HOME`) plus a root
  and `/dev` that the sandbox user would otherwise own writably; these are
  pre-created or chmod'ed read-only (0555) -- traversable for the binds, but
  accepting no writes.
- **Bounded by rlimits the worker cannot raise.** bwrap >= 0.6 removed its
  rlimit options, so a fixed, trusted bootstrap sets RLIMIT_AS / RLIMIT_CPU /
  RLIMIT_NPROC=1 / RLIMIT_CORE=0 / RLIMIT_NOFILE before `execv`-ing the real
  worker command (soft=hard, and the sandbox user namespace has no
  CAP_SYS_RESOURCE in the initial namespace, so the worker cannot raise them).
  RLIMIT_NPROC=1 means the worker itself occupies the only process slot:
  fork() fails with EAGAIN -- the analogue of the Job Object's
  ActiveProcessLimit=1. RLIMIT_CORE=0 keeps evidence-bearing core dumps off
  disk. The service adds the wall-clock deadline and the output-size cap and
  kills the sandbox on any breach, as on Windows.
- **Short-lived and tied to the service.** `--die-with-parent` SIGKILLs the
  worker when bwrap or the service dies (kill-on-close analogue);
  `--new-session` detaches it from any controlling terminal; a `finally`
  block kills it on every error path.

The worker's output is untrusted; `ingest.py` validates it before anything is
persisted. Paths are never logged.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":  # pragma: no cover - never imported on Windows
    raise ImportError("Linux only")

import os  # noqa: E402
import resource  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402

from .sandbox import (  # noqa: E402
    SandboxLimits,
    SandboxResult,
    WorkerCommand,
    worker_command,
)

#: Preferred locations of the distro bubblewrap binary. A bwrap found via
#: `which` (snap/nix) will fail the probe on a userns-restricting distro and
#: the app fails closed. Version floor: bwrap >= 0.9.0 (tested 0.9.0;
#: `--size` needs >= 0.8.0, `--chmod` >= 0.9.0). There is deliberately no
#: runtime version check: an older bwrap errors on the unknown flag, the
#: probe returns False and parsing fails closed.
_BWRAP_CANDIDATES = ("/usr/bin/bwrap", "/bin/bwrap", "/usr/local/bin/bwrap")


class SandboxSetupError(RuntimeError):
    """The sandbox could not be prepared. The message is a fixed category."""


def _resolve_bwrap() -> Path | None:
    for candidate in _BWRAP_CANDIDATES:
        p = Path(candidate)
        if p.is_file():
            return p
    which = shutil.which("bwrap")
    return Path(which).resolve() if which else None


def _limits_bootstrap(command: WorkerCommand, limits: SandboxLimits) -> str:
    """The fixed, trusted command the sandbox actually runs: it applies the
    PD-016 rlimits and then `execv`s the real worker command.

    bwrap >= 0.6 has no rlimit options, so the limits are set from inside.
    The bootstrap is fixed code; the untrusted worker only starts after the
    limits are in force. soft == hard for every limit, and raising a hard
    limit requires CAP_SYS_RESOURCE in the *initial* user namespace, which
    the sandboxed process does not have (its capabilities are confined to
    the new one) -- the worker cannot undo the bounds. `execv` preserves
    rlimits, env and cwd and creates no extra process, so the one-process
    property (RLIMIT_NPROC=1) survives: the worker itself occupies the only
    process slot and fork() fails with EAGAIN. `Popen(preexec_fn=...)` was
    rejected as the alternative: it is documented unsafe in multithreaded
    parents and the service is multithreaded; this bootstrap runs
    single-threaded inside the sandbox child instead.
    """
    argv = [str(command.executable), *command.args]
    cpu = int(limits.cpu_seconds)
    return (
        "import os,resource;"
        f"resource.setrlimit(resource.RLIMIT_AS,({limits.memory_bytes},{limits.memory_bytes}));"
        f"resource.setrlimit(resource.RLIMIT_CPU,({cpu},{cpu}));"
        "resource.setrlimit(resource.RLIMIT_NPROC,(1,1));"
        "resource.setrlimit(resource.RLIMIT_CORE,(0,0));"
        "resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));"
        f"os.execv({argv[0]!r},{argv!r})"
    )


_SYSTEM_BINDS = (Path("/usr"), Path("/lib"), Path("/lib64"))


def _ancestor_dirs(command: WorkerCommand) -> list[Path]:
    """Parent directories of the runtime binds, deduplicated, ordered
    top-down so no `--dir` ever needs an implicitly created parent.

    bwrap creates missing parents of a bind target itself, but as mode-0700
    directories owned by the sandbox user -- writable, which would defeat
    write confinement (a file could be planted in an unbound ancestor such
    as `$HOME`). Pre-creating them read-only (0555, before the binds) keeps
    them traversable but not writable. Ancestors covered by a system bind
    already exist inside the sandbox and are skipped.
    """
    candidates: list[Path] = []
    for d in _binds(command):
        for parent in d.parents:
            if parent == Path("/") or parent in candidates:
                continue
            if any(s == parent or s in parent.parents for s in _SYSTEM_BINDS):
                continue  # exists inside via the system read-only binds
            candidates.append(parent)
    candidates.sort(key=lambda p: (len(p.parts), str(p)))  # shallowest first
    return candidates


def _binds(command: WorkerCommand) -> list[Path]:
    """Program directories to ro-bind, deduplicated against the system binds."""
    resolved = [p.resolve() for p in command.runtime_dirs]
    out: list[Path] = []
    for p in resolved:
        if any(p == s or s in p.parents for s in _SYSTEM_BINDS):
            continue  # already covered by the system read-only binds
        if p not in out:
            out.append(p)
    return out


_SCRATCH_SIZE_BYTES = 128 * 1024 * 1024


def _bwrap_argv(bwrap: Path, command: WorkerCommand, limits: SandboxLimits) -> list[str]:
    argv = [
        str(bwrap),
        "--unshare-all",  # pid, net, user, ipc, uts, cgroup, mount
        "--die-with-parent",  # SIGKILL when bwrap or the service dies
        "--new-session",  # no controlling terminal
        "--clearenv",
        "--dev",
        "/dev",  # minimal devtmpfs (null/zero/urandom)
        "--proc",
        "/proc",  # procfs scoped to the sandbox pid namespace
    ]
    # The ONLY writable location: a 128 MiB, mode-0700 tmpfs. --perms/--size
    # apply to the following --tmpfs; --chmod is redundant belt-and-braces so
    # the outcome is deterministic regardless of which one this bwrap honors.
    argv += ["--perms", "0700", "--size", str(_SCRATCH_SIZE_BYTES)]
    argv += ["--tmpfs", "/scratch", "--chmod", "0700", "/scratch"]
    argv += ["--chdir", "/scratch"]
    argv += ["--setenv", "TMPDIR", "/scratch"]
    argv += ["--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    argv += ["--setenv", "PYTHONIOENCODING", "utf-8"]
    argv += ["--setenv", "PYTHONUTF8", "1"]
    # Pre-create the binds' parent directories read-only (see
    # _ancestor_dirs): bwrap's implicitly created parents are writable,
    # which would defeat write confinement (e.g. a file planted in an
    # unbound $HOME ancestor).
    for ancestor in _ancestor_dirs(command):
        argv += ["--perms", "0555", "--dir", str(ancestor)]
    for d in _binds(command):
        argv += ["--ro-bind", str(d), str(d)]
    argv += ["--ro-bind", "/usr", "/usr"]
    argv += ["--ro-bind-try", "/lib", "/lib"]
    argv += ["--ro-bind-try", "/lib64", "/lib64"]
    # bwrap creates the sandbox root, /dev and /dev/shm writable and owned
    # by the sandbox user; --chmod is applied after all mounts, so this
    # leaves /scratch as the only writable location. /dev keeps its device
    # nodes (null/zero/urandom are world devices, not filesystem writes).
    argv += ["--chmod", "0555", "/", "--chmod", "0555", "/dev"]
    # Not a temp file (S108): /dev/shm is made read-only here.
    argv += ["--chmod", "0555", "/dev/shm"]  # noqa: S108
    argv += [
        "--",
        str(command.executable),
        "-I",
        "-B",
        "-c",
        _limits_bootstrap(command, limits),
    ]
    return argv


def run_contained(
    command: WorkerCommand,
    input_path: Path,
    scratch_root: Path,
    limits: SandboxLimits,
) -> SandboxResult:
    """Runs `command` inside a bubblewrap sandbox, with the already-open
    read-only handle to `input_path` as its stdin. Never raises for anything
    the worker does; setup problems raise `SandboxSetupError` (a fixed
    category)."""
    # scratch_root is deliberately unused: the scratch is a per-parse tmpfs
    # inside the sandbox, never a host directory (a disk-backed bind fallback
    # would use it -- see DECISIONS.md D-028).
    if getattr(sys, "frozen", False):
        # No frozen Linux build exists yet; a frozen exe cannot run the
        # limits bootstrap, and bwrap alone does not bound memory/CPU.
        # Fail closed rather than run unbounded (packaging follow-up owns this).
        raise SandboxSetupError("linux_frozen_bounds_unimplemented")
    bwrap = _resolve_bwrap()
    if bwrap is None:
        return SandboxResult("launch_failed", None, b"")
    argv = _bwrap_argv(bwrap, command, limits)
    in_fd = os.open(input_path, os.O_RDONLY)  # read-only handle, like Windows
    try:
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no PATH lookup
                argv,
                stdin=in_fd,  # the evidence file, as a handle only
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # parity with Windows' NUL (T-10)
                env={},  # bwrap gets nothing; worker env is --clearenv+4
                bufsize=0,  # raw reads, like Windows' os.read loop
                close_fds=True,  # default: the child gets exactly fds 0,1,2
            )
        except OSError as exc:
            # e.g. bwrap vanished after the probe passed; errno is a diagnostic
            # code, not content.
            return SandboxResult("launch_failed", exc.errno, b"")
        try:
            return _collect(proc, limits)
        except BaseException:
            # Mirror Windows' TerminateJobObject-in-finally: nothing survives
            # an error path; --die-with-parent chains the kill to the worker.
            try:
                proc.kill()
                proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
                pass
            raise
    finally:
        os.close(in_fd)  # the child holds its own reference; EOF on exit


def _decode_exit_code(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return -1


def _signaled(status: int, sig: signal.Signals) -> bool:
    return os.WIFSIGNALED(status) and os.WTERMSIG(status) == sig


def _collect(proc: subprocess.Popen[bytes], limits: SandboxLimits) -> SandboxResult:
    chunks: list[bytes] = []
    overflow = threading.Event()
    done = threading.Event()
    status_info: list[tuple[int, int, resource.struct_rusage]] = []  # (pid, status, rusage)

    def reader() -> None:
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover - stdout is PIPE
            return
        total = 0
        try:
            while True:
                chunk = stdout.read(65536)  # bufsize=0 -> one read syscall
                if not chunk:
                    return
                total += len(chunk)
                if total > limits.output_bytes:
                    overflow.set()
                    proc.kill()  # SIGKILL bwrap -> worker dies (die-with-parent)
                    return
                chunks.append(chunk)
        except (OSError, ValueError):
            return

    def reaper() -> None:
        try:
            pid, status, rusage = os.wait4(proc.pid, 0)
            status_info.append((pid, status, rusage))
        except (ChildProcessError, OSError):
            pass
        finally:
            done.set()

    thread = threading.Thread(target=reader, name="parser-output", daemon=True)
    watcher = threading.Thread(target=reaper, name="parser-reaper", daemon=True)
    thread.start()
    watcher.start()
    timed_out = not done.wait(limits.wall_seconds)
    if timed_out:
        proc.kill()
        done.wait(5)
    thread.join(timeout=5)
    watcher.join(timeout=5)

    status = status_info[0][1] if status_info else -1
    rusage = status_info[0][2] if status_info else None
    exit_code = _decode_exit_code(status)  # WEXITSTATUS, or 128+WTERMSIG
    # The reaper owns the wait; record the outcome so Popen.__del__ is quiet.
    proc.returncode = exit_code
    output = b"".join(chunks)
    if overflow.is_set():
        return SandboxResult("output_limit", exit_code, b"")
    if timed_out:
        return SandboxResult("timeout", exit_code, b"")
    if _signaled(status, signal.SIGXCPU) or exit_code == 128 + signal.SIGXCPU:
        return SandboxResult("cpu_limit", exit_code, b"")
    if (
        _signaled(status, signal.SIGKILL)
        and rusage is not None
        and rusage.ru_maxrss * 1024 >= limits.memory_bytes * 0.9
    ):
        # Defensive parity with Windows' PeakProcessMemoryUsed heuristic:
        # RLIMIT_AS normally surfaces as MemoryError inside the worker
        # (exited 0 with detail=memory_limit), so this is rare.
        return SandboxResult("memory_limit", exit_code, b"")
    return SandboxResult("exited", exit_code, output)


_probe_lock = threading.Lock()
_probe_result: bool | None = None


def is_available() -> bool:
    """True only if bwrap exists AND one full contained worker run succeeds
    (user namespaces permitted, mounts work, the worker protocol works).
    Cached for the process lifetime; a failure stays failed until restart."""
    global _probe_result
    if _probe_result is not None:
        return _probe_result
    with _probe_lock:
        if _probe_result is None:
            _probe_result = _probe_once()
        return _probe_result


def reset_probe_cache() -> None:
    """TEST ONLY: forces the next is_available() to re-probe."""
    global _probe_result
    with _probe_lock:
        _probe_result = None


def _probe_once() -> bool:
    try:
        bwrap = _resolve_bwrap()
        if bwrap is None:
            return False
        from . import limits as evidence_limits

        with tempfile.TemporaryDirectory(prefix="cg-sandbox-probe-") as tmp:
            root = Path(tmp)
            input_path = root / "probe.txt"
            input_path.write_bytes(b"")  # empty file, no content
            result = run_contained(
                worker_command("txt"),
                input_path,
                root / "scratch",
                evidence_limits.WORKER_LIMITS,
            )
        return result.outcome == "exited" and result.exit_code == 0
    except Exception:  # noqa: BLE001 - any surprise means "unavailable"
        return False
