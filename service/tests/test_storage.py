"""S2-01: database location resolution (owner decision PD-006 / A-2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from controlgauge_service import storage


def test_windows_uses_local_appdata_not_roaming(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming")}
    path = storage.default_data_dir(platform="win32", environ=env)
    assert path == tmp_path / "Local" / "ControlGauge"


def test_windows_without_localappdata_falls_back_under_home() -> None:
    path = storage.default_data_dir(platform="win32", environ={})
    assert path == Path.home() / "AppData" / "Local" / "ControlGauge"


def test_macos_uses_application_support() -> None:
    path = storage.default_data_dir(platform="darwin", environ={})
    assert path == Path.home() / "Library" / "Application Support" / "ControlGauge"


def test_linux_honors_absolute_xdg_data_home(tmp_path: Path) -> None:
    path = storage.default_data_dir(platform="linux", environ={"XDG_DATA_HOME": str(tmp_path)})
    assert path == tmp_path / "controlgauge"


def test_linux_ignores_relative_xdg_data_home() -> None:
    path = storage.default_data_dir(platform="linux", environ={"XDG_DATA_HOME": "relative"})
    assert path == Path.home() / ".local" / "share" / "controlgauge"


def test_override_must_be_absolute() -> None:
    with pytest.raises(storage.StorageLocationError, match="not_absolute"):
        storage.resolve_data_dir(environ={storage.DATA_DIR_ENV_VAR: "relative/dir"})


@pytest.mark.parametrize(
    "raw",
    [
        r"\\fileserver\share\cg",
        r"\\?\UNC\fileserver\share\cg",
        r"\\?\C:\cg",
        r"\\.\pipe\x",
        "//fileserver/share/cg",
    ],
)
def test_override_rejects_unc_and_device_paths(raw: str) -> None:
    with pytest.raises(storage.StorageLocationError, match="data_dir_override_remote") as info:
        storage.resolve_data_dir(environ={storage.DATA_DIR_ENV_VAR: raw})
    assert "fileserver" not in str(info.value)


def test_override_rejects_a_mapped_network_drive() -> None:
    with pytest.raises(storage.StorageLocationError, match="data_dir_override_remote"):
        storage.check_override_is_local(r"Z:\cg", platform="win32", drive_type=lambda _r: 4)


def test_override_accepts_a_local_fixed_drive() -> None:
    seen: list[str] = []

    def probe(root: str) -> int:
        seen.append(root)
        return 3  # DRIVE_FIXED

    storage.check_override_is_local(r"C:\cg", platform="win32", drive_type=probe)
    assert seen == ["C:\\"]


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="real GetDriveTypeW probe")
def test_real_drive_probe_accepts_the_temp_directory(tmp_path: Path) -> None:
    storage.check_override_is_local(str(tmp_path))


def test_override_is_used_when_absolute(tmp_path: Path) -> None:
    resolved = storage.resolve_data_dir(environ={storage.DATA_DIR_ENV_VAR: str(tmp_path)})
    assert resolved == tmp_path


def test_ensure_data_dir_creates_nested_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert storage.ensure_data_dir(target) == target
    assert target.is_dir()


def test_ensure_data_dir_rejects_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(storage.StorageLocationError):
        storage.ensure_data_dir(blocker)


def test_storage_errors_never_carry_the_path(tmp_path: Path) -> None:
    blocker = tmp_path / "secret-user-name"
    blocker.write_text("x")
    with pytest.raises(storage.StorageLocationError) as info:
        storage.ensure_data_dir(blocker)
    assert "secret-user-name" not in str(info.value)
