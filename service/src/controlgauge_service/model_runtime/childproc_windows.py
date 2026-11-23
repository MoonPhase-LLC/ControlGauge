"""Windows backend for the model-server child (S5-02, PD-024).

Created **suspended**, assigned to a kill-on-close Job Object, then
resumed: if this service process dies, the Job handle dies with it and
the kernel kills the child (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``) --
the die-with-parent analogue. No AppContainer: llama-server is a trusted
runtime binary parsing a model file (trust decision per
``MODEL_RUNTIME.md`` §7/T-08), unlike the evidence parsers (D-022).

Imported only on Windows (guarded ``ImportError``, pattern:
``evidence/_win32.py``). Minimal ctypes bindings of our own -- sharing
prototype objects with the parser sandbox would let the two modules
mutate each other's signatures.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Any

if sys.platform != "win32":  # pragma: no cover - guarded by callers
    raise ImportError("Windows only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
WAIT_OBJECT_0 = 0x00000000
_STILL_ACTIVE = 259

#: Bounded wait in ``close()`` for the (already requested) Job
#: termination to complete, so the final exit code can be captured
#: before the process handle is released. ``stop()`` terminates first,
#: so in the normal path the wait returns immediately.
_CLOSE_CAPTURE_WAIT_MS = 2000


class STARTUPINFOW(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _proto(func: Any, restype: Any, *argtypes: Any) -> Any:
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


CreateProcessW = _proto(
    kernel32.CreateProcessW,
    wintypes.BOOL,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
)
CreateJobObjectW = _proto(
    kernel32.CreateJobObjectW, wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR
)
SetInformationJobObject = _proto(
    kernel32.SetInformationJobObject,
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
)
AssignProcessToJobObject = _proto(
    kernel32.AssignProcessToJobObject, wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE
)
TerminateJobObject = _proto(
    kernel32.TerminateJobObject, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT
)
WaitForSingleObject = _proto(
    kernel32.WaitForSingleObject, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD
)
GetExitCodeProcess = _proto(
    kernel32.GetExitCodeProcess, wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
)
CloseHandle = _proto(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
TerminateProcess = _proto(kernel32.TerminateProcess, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
ResumeThread = _proto(kernel32.ResumeThread, wintypes.DWORD, wintypes.HANDLE)


def _environment_block(env: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    """Windows environment block: sorted ``KEY=VALUE`` strings, double-NUL
    terminated (CREATE_UNICODE_ENVIRONMENT)."""
    text = "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0"
    return ctypes.create_unicode_buffer(text, len(text))


def _null_std_handles() -> tuple[int, int, int, int]:
    """Three inheritable handles to the NUL device for the child's stdio
    (llama-server output is discarded -- T-10)."""
    fd = os.open(os.devnull, os.O_RDWR | os.O_BINARY)
    handle = msvcrt.get_osfhandle(fd)
    os.set_handle_inheritable(handle, True)
    return fd, int(handle), int(handle), int(handle)


class WindowsChild:
    """One Windows llama-server child. Liveness/termination mirror the
    POSIX ``Popen`` surface the provider expects."""

    def __init__(self, process_handle: int, job_handle: int, pid: int) -> None:
        self._process = process_handle
        self._job = job_handle
        self.pid = pid
        #: Exit code captured in ``close()``; ``poll()`` answers from it
        #: once the handles are gone.
        self._final_code: int | None = None

    def poll(self) -> int | None:
        if self._process == 0:
            # Handles released by close(): answer from the captured exit
            # code. A stopped child must never read as "running" -- the
            # POSIX ``Popen`` surface keeps answering after stop, and
            # ``provider.stop()`` closes this handle before callers poll
            # (found by test_stop_terminates_child on windows-latest).
            return self._final_code
        code = wintypes.DWORD()
        if not GetExitCodeProcess(self._process, ctypes.byref(code)):
            return None
        if code.value == _STILL_ACTIVE:
            return None
        return int(code.value)

    def terminate(self, grace_s: float) -> None:
        """Job termination (kills every process in the Job), then a
        bounded wait. Idempotent."""
        TerminateJobObject(self._job, 1)
        WaitForSingleObject(self._process, int(grace_s * 1000))

    def close(self) -> None:
        """Closes the Job handle (KILL_ON_JOB_CLOSE kills a still-running
        child) and the process handle. Idempotent. The final exit code is
        captured BEFORE the process handle disappears, so ``poll()``
        keeps reporting the true state afterwards instead of mapping the
        invalid handle to None ("still running")."""
        if self._job:
            TerminateJobObject(self._job, 1)
            CloseHandle(self._job)
            self._job = 0
        if self._process:
            WaitForSingleObject(self._process, _CLOSE_CAPTURE_WAIT_MS)
            code = wintypes.DWORD()
            if GetExitCodeProcess(self._process, ctypes.byref(code)) and (
                code.value != _STILL_ACTIVE
            ):
                self._final_code = int(code.value)
            CloseHandle(self._process)
            self._process = 0


def spawn(argv: list[str], env: dict[str, str]) -> WindowsChild:
    """Spawn ``argv`` suspended, put it in a kill-on-close Job, resume it.
    Returns a ``WindowsChild``; raises ``OSError`` on any setup failure
    (the provider maps that to ``start_failed``)."""

    def _fail(message: str) -> OSError:
        return OSError(ctypes.get_last_error() or 0, message)

    fd, std_in, std_out, std_err = _null_std_handles()
    try:
        job = CreateJobObjectW(None, None)
        if not job:
            raise _fail("CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            CloseHandle(job)
            raise _fail("SetInformationJobObject")

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput = std_in
        startup.hStdOutput = std_out
        startup.hStdError = std_err
        cmdline = subprocess.list2cmdline(argv)
        env_block = _environment_block(env)
        proc_info = PROCESS_INFORMATION()
        flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
        if not CreateProcessW(
            None,  # application name: taken from the command line
            cmdline,
            None,
            None,
            True,  # inherit the NUL std handles
            flags,
            ctypes.cast(env_block, ctypes.c_void_p),
            None,
            ctypes.byref(startup),
            ctypes.byref(proc_info),
        ):
            CloseHandle(job)
            raise _fail("CreateProcessW")
        try:
            # Created suspended: it cannot run unconfined for even an
            # instant. If assignment fails, kill it before resuming.
            if not AssignProcessToJobObject(job, proc_info.hProcess):
                TerminateJobObject(job, 1)
                TerminateProcess(proc_info.hProcess, 1)
                CloseHandle(proc_info.hProcess)
                raise _fail("AssignProcessToJobObject")
            # Resume only after the Job assignment succeeded; the thread
            # handle is released in the finally below, so the resume must
            # happen while it is still held.
            ResumeThread(proc_info.hThread)
        finally:
            CloseHandle(proc_info.hThread)
        child = WindowsChild(int(proc_info.hProcess), int(job), int(proc_info.dwProcessId))
        # Ownership moved to the child object; do not double-close here.
        job = 0
        proc_info.hProcess = 0
        return child
    finally:
        if job:
            TerminateJobObject(job, 1)
            CloseHandle(job)
        os.close(fd)
