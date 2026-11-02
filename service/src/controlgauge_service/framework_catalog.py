"""Framework *metadata* registry (S2-02, owner decision PD-006).

Reads `frameworks/<dir>/framework.json` files -- identity metadata only
(`id`, `name`, `version`, `source_reference`) -- and upserts one `framework`
row per file at startup. Nothing NIST-specific is coded here: adding a
framework means adding a directory, not changing code
(`docs/ARCHITECTURE.md` §8). No control, family or enhancement content is
read or written; the real catalog import is Sprint 3 (`OPEN_QUESTIONS.md`
C-3), and its file format may extend `framework.json`.

Framework files are untrusted input (`CLAUDE.md`): each is size-limited,
strictly validated, and skipped with a fixed-category warning if invalid.
Rows are never deleted here -- an assessment may reference them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from .db.engine import Database
from .db.models import ID_MAX, Framework

logger = logging.getLogger("controlgauge_service.frameworks")

METADATA_FILENAME = "framework.json"
_MAX_METADATA_BYTES = 64 * 1024


class FrameworkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$", min_length=1, max_length=ID_MAX)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    source_reference: str | None = Field(default=None, max_length=500)


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
            metadata = FrameworkMetadata.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
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


def sync_from_default_location(database: Database) -> int:
    root = default_frameworks_root()
    if root is None:
        logger.warning("framework_metadata_directory_missing")
        frameworks: list[FrameworkMetadata] = []
    else:
        frameworks = load_framework_metadata(root)
    count = sync_frameworks(database, frameworks)
    logger.info("frameworks_synced", extra={"framework_count": count})
    return count
