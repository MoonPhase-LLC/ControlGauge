"""Framework registry: `frameworks/<dir>/framework.json` manifests.

Each manifest gives a framework's identity (`id`, `name`, `version`,
`source_reference`) and, optionally (Sprint 3), the pinned source files of
its catalog and published control selections ("profiles"): path, SHA-256,
release version and provenance. At startup the identity is upserted as a
`framework` row and the catalog is imported by `catalog_import.py`.
Nothing framework-specific is coded here: adding a framework means adding
a directory, not changing code (`docs/ARCHITECTURE.md` §8).

Manifests are untrusted input (`CLAUDE.md`): each is size-limited, strictly
validated, and skipped with a fixed-category warning if invalid. Rows are
never deleted here -- an assessment may reference them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from .db.engine import Database
from .db.models import ID_MAX, IDENTIFIER_MAX, Framework

logger = logging.getLogger("controlgauge_service.frameworks")

METADATA_FILENAME = "framework.json"
_MAX_METADATA_BYTES = 64 * 1024


_SHA256 = r"^[0-9a-f]{64}$"
#: A relative path inside the framework's own directory: segments that start
#: with a letter, digit or underscore (so never `.` or `..`), joined by `/`.
#: No drive, root or backslash can match. The loader also re-checks that
#: the resolved file stays inside the directory.
_RELATIVE_PATH = r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$"
_HTTPS_URL = r"^https://[^\s]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceProvenance(_Strict):
    repository_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS_URL)
    release_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS_URL)
    tag: str | None = Field(default=None, min_length=1, max_length=100)
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    file_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS_URL)


class CatalogSource(_Strict):
    format: Literal["oscal-catalog-json"]
    path: str = Field(max_length=255, pattern=_RELATIVE_PATH)
    sha256: str = Field(pattern=_SHA256)
    release_version: str = Field(min_length=1, max_length=100)
    source: SourceProvenance
    license_notice: str = Field(min_length=1, max_length=2000)
    attribution: str = Field(min_length=1, max_length=2000)


class ProfileSource(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$", min_length=1, max_length=IDENTIFIER_MAX)
    format: Literal["oscal-profile-json"]
    path: str = Field(max_length=255, pattern=_RELATIVE_PATH)
    sha256: str = Field(pattern=_SHA256)
    file_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS_URL)


class FrameworkMetadata(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$", min_length=1, max_length=ID_MAX)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    source_reference: str | None = Field(default=None, max_length=500)
    catalog: CatalogSource | None = None
    profiles: tuple[ProfileSource, ...] = Field(default=(), max_length=50)
    #: The manifest's own directory (set by the loader, not read from JSON).
    directory: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _profiles_need_a_catalog(self) -> FrameworkMetadata:
        if self.profiles and self.catalog is None:
            raise ValueError("profiles_without_catalog")
        ids = [p.id for p in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_profile_id")
        return self


def default_frameworks_root() -> Path | None:
    """`frameworks/` shipped with the app. Frozen (PyInstaller) builds carry
    it next to the bundle's data (`scripts/package-windows.ps1`); a source
    checkout finds the repository's own `frameworks/` directory."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        candidate = Path(frozen_root) / "frameworks"
        return candidate if candidate.is_dir() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frameworks"
        if candidate.is_dir() and (parent / "service").is_dir():
            return candidate
    return None


def load_framework_metadata(root: Path) -> list[FrameworkMetadata]:
    found: dict[str, FrameworkMetadata] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        path = directory / METADATA_FILENAME
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_METADATA_BYTES:
                raise ValueError("too_large")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or "directory" in raw:
                raise ValueError("invalid_shape")
            metadata = FrameworkMetadata.model_validate({**raw, "directory": directory})
        except (OSError, ValueError, ValidationError):
            # ValidationError is a ValueError subclass; listed for clarity.
            logger.warning("framework_metadata_invalid", extra={"directory": directory.name})
            continue
        if metadata.id in found:
            logger.warning("framework_metadata_duplicate_id", extra={"directory": directory.name})
            continue
        found[metadata.id] = metadata
    return list(found.values())


def sync_frameworks(database: Database, frameworks: list[FrameworkMetadata]) -> int:
    """Inserts missing frameworks and refreshes changed metadata. Returns the
    number of frameworks present after syncing."""
    with database.session(write=True) as session:
        for meta in frameworks:
            row = session.get(Framework, meta.id)
            if row is None:
                session.add(
                    Framework(
                        id=meta.id,
                        name=meta.name,
                        version=meta.version,
                        source_reference=meta.source_reference,
                    )
                )
            else:
                row.name = meta.name
                row.version = meta.version
                row.source_reference = meta.source_reference
        session.flush()
        return len(session.scalars(select(Framework.id)).all())


def sync_from_default_location(database: Database, root: Path | None = None) -> int:
    """Registers every framework under `root` (default: the shipped
    `frameworks/`) and imports or verifies each declared catalog."""
    if root is None:
        root = default_frameworks_root()
    if root is None:
        logger.warning("framework_metadata_directory_missing")
        frameworks: list[FrameworkMetadata] = []
    else:
        frameworks = load_framework_metadata(root)
    count = sync_frameworks(database, frameworks)
    logger.info("frameworks_synced", extra={"framework_count": count})

    # Imported here, not at module top, to keep the importer's own imports
    # (and their cost) out of code paths that only read manifests.
    from .catalog_import import sync_catalogs

    sync_catalogs(database, frameworks)
    return count
