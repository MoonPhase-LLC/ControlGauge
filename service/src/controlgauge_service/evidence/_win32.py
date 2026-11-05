"""Minimal ctypes bindings for the Windows parser sandbox (S4, PD-016).

Only the calls `sandbox_windows.py` needs: AppContainer/capability SIDs,
DACL edits, extended process creation (security capabilities, explicit
handle list, LPAC policy) and Job Objects. Imported only on Windows.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

if sys.platform != "win32":  # pragma: no cover - guarded by callers
    raise ImportError("Windows only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)

PSID = ctypes.c_void_p
HRESULT = ctypes.c_long
SIZE_T = ctypes.c_size_t

# --- constants -------------------------------------------------------------

SE_GROUP_ENABLED = 0x00000004

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 0x01

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF

JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9

JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JOB_OBJECT_UILIMIT_ALL = 0x000000FF

# ACL editing
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
GRANT_ACCESS = 1
SUB_CONTAINERS_AND_OBJECTS_INHERIT = 0x3
NO_INHERITANCE = 0x0
TRUSTEE_IS_SID = 0
TRUSTEE_IS_UNKNOWN = 0
NO_MULTIPLE_TRUSTEE = 0
ERROR_SUCCESS = 0

FILE_GENERIC_READ = 0x00120089
FILE_GENERIC_EXECUTE = 0x001200A0
FILE_ALL_ACCESS = 0x001F01FF

ERROR_NOT_ENOUGH_QUOTA = 1816
STATUS_QUOTA_EXCEEDED = 0xC0000044

# --- structures --------------------------------------------------------------


class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("Sid", PSID), ("Attributes", wintypes.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("AppContainerSid", PSID),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):  # noqa: N801
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


class STARTUPINFOEXW(ctypes.Structure):  # noqa: N801
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):  # noqa: N801
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


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):  # noqa: N801
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


class TRUSTEE_W(ctypes.Structure):  # noqa: N801
    pass


TRUSTEE_W._fields_ = [
    ("pMultipleTrustee", ctypes.POINTER(TRUSTEE_W)),
    ("MultipleTrusteeOperation", ctypes.c_int),
    ("TrusteeForm", ctypes.c_int),
    ("TrusteeType", ctypes.c_int),
    ("ptstrName", ctypes.c_void_p),
]


class EXPLICIT_ACCESS_W(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("grfAccessPermissions", wintypes.DWORD),
        ("grfAccessMode", ctypes.c_int),
        ("grfInheritance", wintypes.DWORD),
        ("Trustee", TRUSTEE_W),
    ]


# --- prototypes ------------------------------------------------------------


def _proto(func: Any, restype: Any, *argtypes: Any) -> Any:
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


DeriveAppContainerSidFromAppContainerName = _proto(
    userenv.DeriveAppContainerSidFromAppContainerName,
    HRESULT,
    wintypes.LPCWSTR,
    ctypes.POINTER(PSID),
)
DeriveCapabilitySidsFromName = _proto(
    kernelbase.DeriveCapabilitySidsFromName,
    wintypes.BOOL,
    wintypes.LPCWSTR,
    ctypes.POINTER(ctypes.POINTER(PSID)),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(ctypes.POINTER(PSID)),
    ctypes.POINTER(wintypes.DWORD),
)
CreateAppContainerProfile = _proto(
    userenv.CreateAppContainerProfile,
    HRESULT,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(PSID),
)
DeleteAppContainerProfile = _proto(userenv.DeleteAppContainerProfile, HRESULT, wintypes.LPCWSTR)
FreeSid = _proto(advapi32.FreeSid, ctypes.c_void_p, PSID)
LocalFree = _proto(kernel32.LocalFree, ctypes.c_void_p, ctypes.c_void_p)
LocalAlloc = _proto(kernel32.LocalAlloc, ctypes.c_void_p, wintypes.UINT, SIZE_T)
TerminateProcess = _proto(kernel32.TerminateProcess, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
LPTR = 0x0040
EqualSid = _proto(advapi32.EqualSid, wintypes.BOOL, PSID, PSID)
GetLengthSid = _proto(advapi32.GetLengthSid, wintypes.DWORD, PSID)
ConvertSidToStringSidW = _proto(
    advapi32.ConvertSidToStringSidW, wintypes.BOOL, PSID, ctypes.POINTER(wintypes.LPWSTR)
)

GetNamedSecurityInfoW = _proto(
    advapi32.GetNamedSecurityInfoW,
    wintypes.DWORD,
    wintypes.LPCWSTR,
    ctypes.c_int,
    wintypes.DWORD,
    ctypes.POINTER(PSID),
    ctypes.POINTER(PSID),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
)
SetNamedSecurityInfoW = _proto(
    advapi32.SetNamedSecurityInfoW,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.DWORD,
    PSID,
    PSID,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
SetEntriesInAclW = _proto(
    advapi32.SetEntriesInAclW,
    wintypes.DWORD,
    wintypes.ULONG,
    ctypes.POINTER(EXPLICIT_ACCESS_W),
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
)
GetExplicitEntriesFromAclW = _proto(
    advapi32.GetExplicitEntriesFromAclW,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.ULONG),
    ctypes.POINTER(ctypes.POINTER(EXPLICIT_ACCESS_W)),
)

InitializeProcThreadAttributeList = _proto(
    kernel32.InitializeProcThreadAttributeList,
    wintypes.BOOL,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(SIZE_T),
)
UpdateProcThreadAttribute = _proto(
    kernel32.UpdateProcThreadAttribute,
    wintypes.BOOL,
    ctypes.c_void_p,
    wintypes.DWORD,
    SIZE_T,
    ctypes.c_void_p,
    SIZE_T,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
DeleteProcThreadAttributeList = _proto(
    kernel32.DeleteProcThreadAttributeList, None, ctypes.c_void_p
)
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
    ctypes.POINTER(STARTUPINFOEXW),
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
QueryInformationJobObject = _proto(
    kernel32.QueryInformationJobObject,
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
AssignProcessToJobObject = _proto(
    kernel32.AssignProcessToJobObject, wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE
)
TerminateJobObject = _proto(
    kernel32.TerminateJobObject, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT
)
ResumeThread = _proto(kernel32.ResumeThread, wintypes.DWORD, wintypes.HANDLE)
WaitForSingleObject = _proto(
    kernel32.WaitForSingleObject, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD
)
GetExitCodeProcess = _proto(
    kernel32.GetExitCodeProcess, wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
)
CloseHandle = _proto(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)


def last_error() -> int:
    return ctypes.get_last_error()
