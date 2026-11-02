"""Where the local SQLite database lives (Sprint 2, S2-01).

Owner decision for Sprint 2 (`docs/planning/DECISION_LOG.md` PD-006,
`docs/OPEN_QUESTIONS.md` A-2): the database lives in the OS-appropriate
**per-user, machine-local** application-data directory. A user-configurable
storage path is deferred.

- Windows: `%LOCALAPPDATA%\\ControlGauge` (Local, not Roaming -- assessment
  data must not sync to other machines through a roaming profile).
- macOS: `~/Library/Application Support/ControlGauge`.
- Other POSIX: `$XDG_DATA_HOME/controlgauge` (default `~/.local/share`).

`CONTROLGAUGE_DATA_DIR` overrides the directory. It is a **developer/test
hook only** (isolating test runs and dev sessions from a real database),
not a product setting: nothing in the UI exposes it, and it confers nothing
an attacker who can already set this user's environment does not have.
Production behavior: the packaged service honors it too (CI's frozen-exe
tests depend on that), because the Tauri app passes its environment to the
service. It must be an absolute, local path: UNC/device paths and mapped
network drives are refused (PD-008 safeguards), and startup fails closed.

The resolved path is never logged (it contains the OS user name); startup
logs record only that the database is ready and its schema revision.
"""

from __future__ import annotations

import ntpath
import os
import sys
from collections.abc import Callable
from pathlib import Path

DATA_DIR_ENV_VAR = "CONTROLGAUGE_DATA_DIR"
DATABASE_FILENAME = "controlgauge.db"
_APP_DIR_NAME = "ControlGauge"
_APP_DIR_NAME_POSIX = "controlgauge"


class StorageLocationError(RuntimeError):
    """The data directory could not be resolved or created. The message is a
    fixed category, never a path."""


def default_data_dir(*, platform: str | None = None, environ: dict[str, str] | None = None) -> Path:
    """The OS-default per-user data directory (no override applied).
    `platform`/`environ` are injectable for tests."""
    plat = platform if platform is not None else sys.platform
    env = environ if environ is not None else dict(os.environ)
    home = Path.home()

    if plat.startswith("win"):
        local = env.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / _APP_DIR_NAME
    if plat == "darwin":
        return home / "Library" / "Application Support" / _APP_DIR_NAME
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / _APP_DIR_NAME_POSIX


_DRIVE_REMOTE = 4  # GetDriveTypeW: network (mapped) drive


def _windows_drive_type(root: str) -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined,unused-ignore]


def check_override_is_local(
    raw: str,
    *,
    platform: str | None = None,
    drive_type: Callable[[str], int] | None = None,
) -> None:
    """PD-008 safeguard: the dev/test override must be a local path. SQLite
    on network storage risks corruption (unreliable locking), so UNC/device
    paths (`\\\\server\\share`, `\\\\?\\...`, `//server/share`) and, on Windows,
    mapped network drives are refused. Fixed reasons only, never the path."""
    plat = platform if platform is not None else sys.platform
    if raw.startswith(("\\\\", "//")):
        raise StorageLocationError("data_dir_override_remote")
    if plat.startswith("win"):
        drive = ntpath.splitdrive(raw)[0]
        if len(drive) == 2 and drive[1] == ":":
            probe = drive_type if drive_type is not None else _windows_drive_type
            if probe(drive + "\\") == _DRIVE_REMOTE:
                raise StorageLocationError("data_dir_override_remote")


def resolve_data_dir(*, environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else dict(os.environ)
    override = env.get(DATA_DIR_ENV_VAR)
    if override:
        check_override_is_local(override)
        path = Path(override)
        if not path.is_absolute():
            raise StorageLocationError("data_dir_override_not_absolute")
        return path
    return default_data_dir(environ=env)


def ensure_data_dir(path: Path) -> Path:
    """Creates the directory if needed (owner-only on POSIX; on Windows the
    per-user profile ACL already restricts it)."""
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise StorageLocationError("data_dir_create_failed") from exc
    if not path.is_dir():
        raise StorageLocationError("data_dir_not_a_directory")
    return path


def database_path(data_dir: Path) -> Path:
    return data_dir / DATABASE_FILENAME
