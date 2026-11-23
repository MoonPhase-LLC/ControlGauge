"""Windows hardware probes (S6-02).

Local-only and best-effort: every probe returns ``None``/empty on any
failure, never an error (``docs/MODEL_RUNTIME.md`` §4). Mechanisms are
stdlib ``ctypes`` only -- ``psutil``/``comtypes``/WMI would each be a new
dependency requiring PyInstaller bundle-list maintenance (the httpx CI
lesson), and DXGI is vendor-agnostic (NVIDIA/AMD/Intel) where WMI
``win32_VideoController`` needs WMI tooling and ``nvidia-smi`` is
vendor-specific.

Isolation rules that keep this module importable (and mypy-clean) on the
Linux CI:

- Module level holds **pure data only**: the ``ctypes.Structure`` mirrors
  of ``MEMORYSTATUSEX``/``DXGI_ADAPTER_DESC`` and constants. No
  win32-only ``ctypes`` attribute is touched at import time.
- ``ctypes.windll`` / ``ctypes.WINFUNCTYPE`` are reached through
  ``getattr`` *inside* the probe functions (the Linux typeshed has no such
  attributes), and every probe starts with a ``sys.platform`` guard so a
  stray call off-Windows degrades to unknown instead of raising.
- Every COM call path is wrapped: any failure yields ``(None, None)`` or
  ``()`` for that field.

The Windows DXGI path is fixture-verified until the owner's Windows
machine runs it (SPRINTS.md exit criterion); ``vendor_from_vendor_id`` and
the parsing helpers are pure and tested on all platforms.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys

from .models import GpuInfo

# --- pure structures and constants (safe on every platform) -------------------


class _MemoryStatusEx(ctypes.Structure):
    """``MEMORYSTATUSEX`` (winbase.h). ``dwLength`` must be set to
    ``sizeof`` before the call."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _Guid(ctypes.Structure):
    """``GUID`` (guiddef.h)."""

    _fields_ = [
        ("Data1", ctypes.c_ulong),  # 32-bit on Windows
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _DxgiAdapterDesc(ctypes.Structure):
    """``DXGI_ADAPTER_DESC`` (dxgi.h), fields in declaration order. Only
    ``Description``/``VendorId``/``DedicatedVideoMemory`` are read; the
    rest is carried for exact layout. ``AdapterLuid`` is spelled out as
    its two 32-bit halves (``DWORD``/``LONG``) so the structure is exact
    regardless of the host platform's ``c_ulong`` width."""

    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuidLowPart", ctypes.c_uint32),
        ("AdapterLuidHighPart", ctypes.c_int32),
    ]


#: IID_IDXGIFactory1 {770AAE78-F26F-4DBA-A829-253C83D1B387}.
_IDXGI_FACTORY1_IID = _Guid(
    0x770AAE78,
    0xF26F,
    0x4DBA,
    (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87),
)

#: PCI vendor IDs -> ``GPU_VENDORS`` members. Pure, unit-tested on all
#: platforms (including 0x1022: AMD's ATI lineage).
_VENDOR_IDS: dict[int, str] = {
    0x10DE: "nvidia",
    0x1002: "amd",
    0x1022: "amd",
    0x8086: "intel",
}

#: COM vtable slots (IUnknown is 0/1/2 everywhere):
#: - ``IDXGIFactory1``: ``EnumAdapters`` inherited at slot 3, ``Release`` at 2.
#: - ``IDXGIAdapter``: ``GetDesc`` at slot 4, ``Release`` at 2.
_SLOT_RELEASE = 2
_SLOT_FACTORY_ENUM_ADAPTERS = 3
_SLOT_ADAPTER_GET_DESC = 4

_S_OK = 0


def vendor_from_vendor_id(vendor_id: int) -> str:
    """The closed vendor vocabulary for a PCI vendor ID; anything not in
    the known set is ``"unknown"`` -- never guessed."""
    return _VENDOR_IDS.get(vendor_id, "unknown")


# --- probes --------------------------------------------------------------------


def memory() -> tuple[int | None, int | None]:
    """Total/available physical RAM in bytes via
    ``GlobalMemoryStatusEx`` (kernel32). Any failure -> ``(None, None)``."""
    if sys.platform != "win32":
        return (None, None)
    try:
        return _memory_via_globalmemorystatusex()
    except Exception:  # noqa: BLE001 - probe failure degrades to unknown
        return (None, None)


def _memory_via_globalmemorystatusex() -> tuple[int | None, int | None]:
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    # B009 is deliberate: the win32-only attribute is reached through
    # getattr so the Linux typeshed (and CI mypy) never sees it.
    windll = getattr(ctypes, "windll")  # noqa: B009
    if int(windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))) == 0:
        return (None, None)
    return (int(status.ullTotalPhys), int(status.ullAvailPhys))


def gpus() -> tuple[GpuInfo, ...]:
    """One ``GpuInfo`` per DXGI adapter (``CreateDXGIFactory1`` ->
    ``EnumAdapters`` -> ``GetDesc``). Any failure -> ``()``: a machine
    where DXGI cannot answer reports *nothing detected*, never an error."""
    if sys.platform != "win32":
        return ()
    try:
        return _enum_dxgi_gpus()
    except Exception:  # noqa: BLE001 - probe failure degrades to empty
        return ()


def _enum_dxgi_gpus() -> tuple[GpuInfo, ...]:
    """The real DXGI walk. Raw vtable dispatch (no comtypes): a COM
    interface pointer's first word is its vtable, and each slot is a
    ``__stdcall`` function whose first argument is the object itself."""
    windll = getattr(ctypes, "windll")  # noqa: B009 - win32-only attribute (see memory)
    winfunctype = getattr(ctypes, "WINFUNCTYPE")  # noqa: B009
    dxgi = windll.dxgi

    # Prototypes (HRESULT is a 32-bit signed long; Release returns ULONG).
    create_factory_proto = winfunctype(
        ctypes.c_long,  # HRESULT
        ctypes.POINTER(_Guid),  # REFIID
        ctypes.POINTER(ctypes.c_void_p),  # void **ppvFactory
    )
    release_proto = winfunctype(ctypes.c_uint, ctypes.c_void_p)
    enum_adapters_proto = winfunctype(
        ctypes.c_long,  # HRESULT
        ctypes.c_void_p,  # this
        ctypes.c_uint,  # Adapter index
        ctypes.POINTER(ctypes.c_void_p),  # IIDXGIAdapter **
    )
    get_desc_proto = winfunctype(
        ctypes.c_long,  # HRESULT
        ctypes.c_void_p,  # this
        ctypes.POINTER(_DxgiAdapterDesc),
    )

    create_factory = create_factory_proto(("CreateDXGIFactory1", dxgi))
    factory = ctypes.c_void_p()
    iid = _Guid.from_buffer_copy(_IDXGI_FACTORY1_IID)
    if int(create_factory(ctypes.byref(iid), ctypes.byref(factory))) != _S_OK or not factory.value:
        return ()

    release_factory = release_proto(_vtbl_slot(factory.value, _SLOT_RELEASE))
    enum_adapters = enum_adapters_proto(_vtbl_slot(factory.value, _SLOT_FACTORY_ENUM_ADAPTERS))

    gpus: list[GpuInfo] = []
    try:
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            if (
                int(enum_adapters(factory.value, index, ctypes.byref(adapter))) != _S_OK
                or not adapter.value
            ):
                break  # DXGI_ERROR_NOT_FOUND / anything else: stop enumerating
            index += 1
            try:
                get_desc = get_desc_proto(_vtbl_slot(adapter.value, _SLOT_ADAPTER_GET_DESC))
                desc = _DxgiAdapterDesc()
                if int(get_desc(adapter.value, ctypes.byref(desc))) == _S_OK:
                    gpus.append(
                        GpuInfo(
                            vendor=vendor_from_vendor_id(int(desc.VendorId)),
                            name=str(desc.Description) or None,
                            vram_bytes=int(desc.DedicatedVideoMemory),
                        )
                    )
            finally:
                release_proto(_vtbl_slot(adapter.value, _SLOT_RELEASE))(adapter.value)
    finally:
        release_factory(factory.value)
    return tuple(gpus)


def _vtbl_slot(obj: int, slot: int) -> int:
    """The raw function pointer at COM vtable ``slot`` of object address
    ``obj`` (a COM object's first member is its vtable pointer)."""
    vtable = ctypes.cast(
        ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p)).contents,
        ctypes.POINTER(ctypes.c_void_p),
    )
    entry: ctypes.c_void_p | None = vtable[slot]
    if entry is None or entry.value is None:
        raise OSError("dxgi_vtable_slot_missing")
    return int(entry.value)


def cpu() -> tuple[str | None, int | None]:
    """``platform.processor()`` -- empty on some Windows installs, so an
    empty value is unknown -- and ``os.cpu_count()``."""
    model = platform.processor() or None
    return (model, os.cpu_count())
