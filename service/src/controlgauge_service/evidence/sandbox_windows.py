"""The parser containment boundary on Windows (Sprint 4, `DECISIONS.md`
D-022, owner decision PD-016 resolving `OPEN_QUESTIONS.md` S-1).

Each parse runs in a fresh, short-lived worker process that is:

- **A less-privileged AppContainer (LPAC) with a per-parse identity.** The
  worker's token carries a new AppContainer SID derived from a random name
  for every parse, no network capabilities, and opts out of the
  `ALL APPLICATION PACKAGES` group. It can therefore open only objects whose
  DACL names its own SID, one of its capability SIDs, or
  `ALL RESTRICTED APPLICATION PACKAGES` (system DLLs). The evidence store,
  database, credentials and user profile grant none of these, so access is
  denied by the OS, not by convention. No network capability is granted,
  so sockets cannot connect, including to loopback.
- **Given only what parsing needs.** The input file is opened read-only by
  the trusted service and passed as the worker's stdin handle; the worker
  never receives a path to evidence. Output goes to a pipe on stdout;
  stderr goes to `NUL`. An explicit handle list (`PROC_THREAD_ATTRIBUTE_
  HANDLE_LIST`) makes these three the only inherited handles, and the
  environment block is built from scratch (no inherited variables or
  secrets). The worker's program files are readable through one named
  capability SID (`controlGaugeParserRuntime`) granted read/execute on
  those directories only. A per-parse scratch directory is the only
  writable location (granted to that parse's SID alone, deleted after).
- **Bounded by a Job Object:** process memory, user-mode CPU time, one
  active process (no children), UI restrictions, kill-on-close; the
  service enforces wall-clock time and the output size, and terminates the
  job on any breach.

The worker's output is untrusted; `ingest.py` validates it before anything
is persisted. Paths are never logged.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - imported only on Windows
    raise ImportError("Windows only")

import ctypes  # noqa: E402
import msvcrt  # noqa: E402
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from . import _win32 as w
from .sandbox import SandboxLimits, SandboxResult, WorkerCommand

CAPABILITY_NAME = "controlGaugeParserRuntime"
_CONTAINER_PREFIX = "ControlGauge.Parser."
#: Well-known LPAC capability: read access to the system registry keys
#: granted to it (the interpreter's startup needs these). Grants nothing
#: on user files or the network.
EXTRA_CAPABILITIES: list[str] = ["registryRead"]


class SandboxSetupError(RuntimeError):
    """The sandbox could not be prepared. The message is a fixed category."""


# --- SIDs and ACLs -----------------------------------------------------------


@dataclass
class _Sid:
    """Owns a SID pointer and how to free it."""

    ptr: int
    free_with_localfree: bool

    def free(self) -> None:
        if self.ptr:
            if self.free_with_localfree:
                w.LocalFree(self.ptr)
            else:
                w.FreeSid(self.ptr)
            self.ptr = 0


def sid_to_string(sid: int) -> str:
    out = wintypes.LPWSTR()
    if not w.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise SandboxSetupError("sid_to_string_failed")
    try:
        return str(out.value)
    finally:
        w.LocalFree(ctypes.cast(out, ctypes.c_void_p))


def _create_container(name: str) -> _Sid:
    """Registers a new AppContainer profile (Windows requires one to launch
    a process in it) and returns its SID. Deleted after the parse by
    `_delete_container`; leftovers from a crash are removed at startup by
    `remove_stale_containers`."""
    sid = w.PSID()
    hr = w.CreateAppContainerProfile(
        name, "ControlGauge parser", "ControlGauge parser sandbox", None, 0, ctypes.byref(sid)
    )
    if hr != 0 or not sid.value:
        raise SandboxSetupError("appcontainer_create_failed")
    return _Sid(int(sid.value), free_with_localfree=False)


def _delete_container(name: str) -> None:
    w.DeleteAppContainerProfile(name)


def _derive_capability_sid(name: str) -> _Sid:
    groups = ctypes.POINTER(w.PSID)()
    group_count = wintypes.DWORD()
    caps = ctypes.POINTER(w.PSID)()
    cap_count = wintypes.DWORD()
    ok = w.DeriveCapabilitySidsFromName(
        name,
        ctypes.byref(groups),
        ctypes.byref(group_count),
        ctypes.byref(caps),
        ctypes.byref(cap_count),
    )
    if not ok or cap_count.value < 1:
        raise SandboxSetupError("capability_sid_failed")
    try:
        # Copy the first capability SID; free everything the API allocated.
        length = w.GetLengthSid(caps[0])
        copy = w.LocalAlloc(w.LPTR, length)
        if not copy:
            raise SandboxSetupError("capability_sid_failed")
        ctypes.memmove(copy, caps[0], length)
        return _Sid(int(copy), free_with_localfree=True)
    finally:
        for i in range(group_count.value):
            w.LocalFree(groups[i])
        for i in range(cap_count.value):
            w.LocalFree(caps[i])
        w.LocalFree(ctypes.cast(groups, ctypes.c_void_p))
        w.LocalFree(ctypes.cast(caps, ctypes.c_void_p))


def _dacl_grants(path: Path, sid: int, mask: int) -> bool:
    """True if `path`'s DACL already has an explicit GRANT for `sid` that
    covers `mask`."""
    dacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    err = w.GetNamedSecurityInfoW(
        str(path),
        w.SE_FILE_OBJECT,
        w.DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if err != w.ERROR_SUCCESS:
        raise SandboxSetupError("read_acl_failed")
    try:
        count = wintypes.ULONG()
        entries = ctypes.POINTER(w.EXPLICIT_ACCESS_W)()
        if w.GetExplicitEntriesFromAclW(dacl, ctypes.byref(count), ctypes.byref(entries)) != 0:
            raise SandboxSetupError("read_acl_failed")
        try:
            for i in range(count.value):
                entry = entries[i]
                if (
                    entry.grfAccessMode == w.GRANT_ACCESS
                    and entry.Trustee.TrusteeForm == w.TRUSTEE_IS_SID
                    and entry.Trustee.ptstrName
                    and w.EqualSid(entry.Trustee.ptstrName, sid)
                    and entry.grfAccessPermissions & mask == mask
                ):
                    return True
            return False
        finally:
            if entries:
                w.LocalFree(ctypes.cast(entries, ctypes.c_void_p))
    finally:
        if sd:
            w.LocalFree(sd)


def _grant(path: Path, sid: int, mask: int, inherit: int) -> None:
    """Adds an ACE granting `mask` to `sid` on `path`, merged with the
    existing DACL (nothing else changes)."""
    dacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    err = w.GetNamedSecurityInfoW(
        str(path),
        w.SE_FILE_OBJECT,
        w.DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if err != w.ERROR_SUCCESS:
        raise SandboxSetupError("read_acl_failed")
    new_dacl = ctypes.c_void_p()
    try:
        access = w.EXPLICIT_ACCESS_W()
        access.grfAccessPermissions = mask
        access.grfAccessMode = w.GRANT_ACCESS
        access.grfInheritance = inherit
        access.Trustee.MultipleTrusteeOperation = w.NO_MULTIPLE_TRUSTEE
        access.Trustee.TrusteeForm = w.TRUSTEE_IS_SID
        access.Trustee.TrusteeType = w.TRUSTEE_IS_UNKNOWN
        access.Trustee.ptstrName = sid
        if w.SetEntriesInAclW(1, ctypes.byref(access), dacl, ctypes.byref(new_dacl)) != 0:
            raise SandboxSetupError("write_acl_failed")
        err = w.SetNamedSecurityInfoW(
            str(path),
            w.SE_FILE_OBJECT,
            w.DACL_SECURITY_INFORMATION,
            None,
            None,
            new_dacl,
            None,
        )
        if err != w.ERROR_SUCCESS:
            raise SandboxSetupError("write_acl_failed")
    finally:
        if new_dacl:
            w.LocalFree(new_dacl)
        if sd:
            w.LocalFree(sd)


_RUNTIME_MASK = w.FILE_GENERIC_READ | w.FILE_GENERIC_EXECUTE
_runtime_lock = threading.Lock()
_runtime_ready: set[str] = set()


def ensure_runtime_access(dirs: Iterable[Path]) -> None:
    """Grants the parser capability read/execute on the worker's program
    directories (inherited by their contents). Idempotent: an existing
    grant is detected and left alone. Only program directories are passed
    here, never data or evidence."""
    cap = _derive_capability_sid(CAPABILITY_NAME)
    try:
        with _runtime_lock:
            for d in dirs:
                key = os.path.normcase(str(d.resolve()))
                if key in _runtime_ready:
                    continue
                if not _dacl_grants(d, cap.ptr, _RUNTIME_MASK):
                    _grant(d, cap.ptr, _RUNTIME_MASK, w.SUB_CONTAINERS_AND_OBJECTS_INHERIT)
                _runtime_ready.add(key)
    finally:
        cap.free()


# --- process launch ------------------------------------------------------------


def _environment_block(scratch: Path) -> ctypes.Array[ctypes.c_wchar]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    entries = [
        f"SystemRoot={system_root}",
        f"TEMP={scratch}",
        f"TMP={scratch}",
        # Windows requires these for AppContainer process creation (it
        # fails with ERROR_ENVVAR_NOT_FOUND otherwise). They point at the
        # scratch directory so no real profile path is exposed.
        f"USERPROFILE={scratch}",
        f"LOCALAPPDATA={scratch}",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONIOENCODING=utf-8",
        "PYTHONUTF8=1",
    ]
    text = "\0".join(sorted(entries, key=str.upper)) + "\0\0"
    return ctypes.create_unicode_buffer(text, len(text))


def _inheritable_handle(fd: int) -> int:
    handle = msvcrt.get_osfhandle(fd)
    os.set_handle_inheritable(handle, True)
    return int(handle)


def run_contained(
    command: WorkerCommand,
    input_path: Path,
    scratch_root: Path,
    limits: SandboxLimits,
) -> SandboxResult:
    """Runs `command` contained, with `input_path` as its stdin. Never
    raises for anything the worker does; setup problems raise
    `SandboxSetupError` (a fixed category)."""
    ensure_runtime_access(command.runtime_dirs)

    name = _CONTAINER_PREFIX + uuid.uuid4().hex
    container = _create_container(name)
    cap = _derive_capability_sid(CAPABILITY_NAME)
    extra = [_derive_capability_sid(n) for n in EXTRA_CAPABILITIES]
    scratch = scratch_root / uuid.uuid4().hex
    fds: list[int] = []
    handles: list[int] = []
    job = None
    try:
        scratch.mkdir(parents=False, exist_ok=False)
        _grant(
            scratch,
            container.ptr,
            w.FILE_ALL_ACCESS,
            w.SUB_CONTAINERS_AND_OBJECTS_INHERIT,
        )

        in_fd = os.open(input_path, os.O_RDONLY | os.O_BINARY)
        fds.append(in_fd)
        read_fd, write_fd = os.pipe()
        fds.extend([read_fd, write_fd])
        null_fd = os.open(os.devnull, os.O_WRONLY | os.O_BINARY)
        fds.append(null_fd)
        std_in = _inheritable_handle(in_fd)
        std_out = _inheritable_handle(write_fd)
        std_err = _inheritable_handle(null_fd)

        # Security capabilities: our container SID + the runtime capability.
        all_caps = [cap, *extra]
        cap_attrs = (w.SID_AND_ATTRIBUTES * len(all_caps))()
        for i, c in enumerate(all_caps):
            cap_attrs[i].Sid = c.ptr
            cap_attrs[i].Attributes = w.SE_GROUP_ENABLED
        sec = w.SECURITY_CAPABILITIES()
        sec.AppContainerSid = container.ptr
        sec.Capabilities = ctypes.cast(cap_attrs, ctypes.POINTER(w.SID_AND_ATTRIBUTES))
        sec.CapabilityCount = len(all_caps)
        handle_list = (wintypes.HANDLE * 3)(std_in, std_out, std_err)
        lpac_policy = wintypes.DWORD(w.PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT)

        size = w.SIZE_T()
        w.InitializeProcThreadAttributeList(None, 3, 0, ctypes.byref(size))
        attr_buf = ctypes.create_string_buffer(size.value)
        if not w.InitializeProcThreadAttributeList(attr_buf, 3, 0, ctypes.byref(size)):
            raise SandboxSetupError("attribute_list_failed")
        try:
            for attribute, value, value_size in (
                (
                    w.PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                    ctypes.byref(sec),
                    ctypes.sizeof(sec),
                ),
                (
                    w.PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    handle_list,
                    ctypes.sizeof(handle_list),
                ),
                (
                    w.PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY,
                    ctypes.byref(lpac_policy),
                    ctypes.sizeof(lpac_policy),
                ),
            ):
                if not w.UpdateProcThreadAttribute(
                    attr_buf,
                    0,
                    attribute,
                    ctypes.cast(value, ctypes.c_void_p),
                    value_size,
                    None,
                    None,
                ):
                    raise SandboxSetupError("attribute_list_failed")

            startup = w.STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(w.STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = w.STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = std_in
            startup.StartupInfo.hStdOutput = std_out
            startup.StartupInfo.hStdError = std_err
            startup.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

            job = _create_job(limits)
            cmdline = ctypes.create_unicode_buffer(
                subprocess.list2cmdline([str(command.executable), *command.args])
            )
            env = _environment_block(scratch)
            info = w.PROCESS_INFORMATION()
            flags = (
                w.EXTENDED_STARTUPINFO_PRESENT
                | w.CREATE_SUSPENDED
                | w.CREATE_NO_WINDOW
                | w.CREATE_UNICODE_ENVIRONMENT
            )
            if not w.CreateProcessW(
                str(command.executable),
                cmdline,
                None,
                None,
                True,
                flags,
                ctypes.cast(env, ctypes.c_void_p),
                str(scratch),
                ctypes.byref(startup),
                ctypes.byref(info),
            ):
                # exit_code carries the Win32 error code (diagnostic, not content).
                return SandboxResult(outcome="launch_failed", exit_code=w.last_error(), output=b"")
        finally:
            w.DeleteProcThreadAttributeList(attr_buf)

        handles.extend([info.hProcess, info.hThread])
        if not w.AssignProcessToJobObject(job, info.hProcess):
            w.TerminateJobObject(job, 1)
            # Never let an unconfined worker run: it was created suspended.
            w.TerminateProcess(info.hProcess, 1)
            return SandboxResult(outcome="launch_failed", exit_code=None, output=b"")
        # The child holds its own copies; close ours so EOF arrives on exit.
        for fd in (in_fd, write_fd, null_fd):
            os.close(fd)
            fds.remove(fd)
        w.ResumeThread(info.hThread)

        return _collect(job, info.hProcess, read_fd, limits)
    finally:
        if job is not None:
            w.TerminateJobObject(job, 1)
            w.CloseHandle(job)
        for h in handles:
            w.CloseHandle(h)
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass
        shutil.rmtree(scratch, ignore_errors=True)
        container.free()
        cap.free()
        for c in extra:
            c.free()
        _delete_container(name)


def _create_job(limits: SandboxLimits) -> int:
    job = w.CreateJobObjectW(None, None)
    if not job:
        raise SandboxSetupError("job_create_failed")
    info = w.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    basic = info.BasicLimitInformation
    basic.LimitFlags = (
        w.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | w.JOB_OBJECT_LIMIT_JOB_MEMORY
        | w.JOB_OBJECT_LIMIT_PROCESS_TIME
        | w.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | w.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | w.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    basic.ActiveProcessLimit = 1
    basic.PerProcessUserTimeLimit = int(limits.cpu_seconds * 10_000_000)
    info.ProcessMemoryLimit = limits.memory_bytes
    info.JobMemoryLimit = limits.memory_bytes
    ui = w.JOBOBJECT_BASIC_UI_RESTRICTIONS(w.JOB_OBJECT_UILIMIT_ALL)
    if not (
        w.SetInformationJobObject(
            job, w.JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        and w.SetInformationJobObject(
            job, w.JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui)
        )
    ):
        w.CloseHandle(job)
        raise SandboxSetupError("job_limits_failed")
    return int(job)


def _peak_memory(job: int) -> int:
    info = w.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if w.QueryInformationJobObject(
        job, w.JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        return int(info.PeakProcessMemoryUsed)
    return 0


def _collect(job: int, process: int, read_fd: int, limits: SandboxLimits) -> SandboxResult:
    chunks: list[bytes] = []
    overflow = threading.Event()

    def reader() -> None:
        total = 0
        try:
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    return
                total += len(chunk)
                if total > limits.output_bytes:
                    overflow.set()
                    w.TerminateJobObject(job, 1)
                    return
                chunks.append(chunk)
        except OSError:
            return

    thread = threading.Thread(target=reader, name="parser-output", daemon=True)
    thread.start()
    waited = w.WaitForSingleObject(process, int(limits.wall_seconds * 1000))
    timed_out = waited == w.WAIT_TIMEOUT
    if timed_out:
        w.TerminateJobObject(job, 1)
        w.WaitForSingleObject(process, 5000)
    thread.join(timeout=5)

    code = wintypes.DWORD()
    exit_code = int(code.value) if w.GetExitCodeProcess(process, ctypes.byref(code)) else None
    output = b"".join(chunks)
    if overflow.is_set():
        return SandboxResult(outcome="output_limit", exit_code=exit_code, output=b"")
    if timed_out:
        return SandboxResult(outcome="timeout", exit_code=exit_code, output=b"")
    # The job's CPU limit terminates the process with a quota status.
    if exit_code in (w.ERROR_NOT_ENOUGH_QUOTA, w.STATUS_QUOTA_EXCEEDED):
        return SandboxResult(outcome="cpu_limit", exit_code=exit_code, output=b"")
    if exit_code != 0 and _peak_memory(job) >= limits.memory_bytes * 0.9:
        return SandboxResult(outcome="memory_limit", exit_code=exit_code, output=b"")
    return SandboxResult(outcome="exited", exit_code=exit_code, output=output)


_MAPPINGS_KEY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows"
    r"\CurrentVersion\AppContainer\Mappings"
)


def remove_stale_containers() -> int:
    """Deletes per-parse AppContainer profiles left behind by an
    interrupted parse (identified by this module's name prefix). Returns
    how many were removed."""
    import winreg

    stale: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MAPPINGS_KEY) as mappings:
            index = 0
            while True:
                try:
                    sid = winreg.EnumKey(mappings, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(mappings, sid) as entry:
                        moniker, _ = winreg.QueryValueEx(entry, "Moniker")
                except OSError:
                    continue
                # Windows stores the moniker lowercased.
                if isinstance(moniker, str) and moniker.lower().startswith(
                    _CONTAINER_PREFIX.lower()
                ):
                    stale.append(moniker)
    except FileNotFoundError:
        return 0
    for moniker in stale:
        _delete_container(moniker)
    return len(stale)
