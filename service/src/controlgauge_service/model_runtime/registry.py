"""Shared model-import persistence (S5-05 logic, extracted S6-04).

The one transactional writer for `model_file` rows: duplicate-aware
(same content + same role refreshes the existing record's verification
timestamp and location instead of inserting), race-safe (the unique
``(sha256_hash, role)`` constraint decides; a lost race re-reads the
winner), and T-10 clean (nothing about the path or filename is logged).

S6-05 extends this module with the `model_source` registry (migration
0006): every registration now writes full provenance 1:1 beside the
`model_file` row, in the SAME transaction -- `origin`, `verification`,
and `files_json` covering ALL files of the model (both shards of a split
GGUF, not just the primary file the runtime is given). On a duplicate
registration the provenance is merged, never silently downgraded:

- `origin`/`verification` reflect the most recent import action;
- `files_json` keeps whichever list covers MORE files (a one-file local
  re-import cannot erase a two-shard download's coverage);
- identity fields (`catalog_model_id`, `source_url`, `revision`,
  `license_notice`) are overwritten only when the new registration
  itself carries them -- plain local re-imports preserve a catalog link.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..db.base import utc_now
from ..db.models import ModelFile, ModelSource

logger = logging.getLogger("controlgauge_service.model_runtime.registry")


@dataclass(frozen=True)
class SourceFile:
    """One file of a model as recorded in `model_source.files_json`."""

    filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ImportProvenance:
    """Registry provenance written 1:1 beside every registered model.

    ``origin``/``verification`` are the fixed vocabularies of migration
    0006 (``db.models.MODEL_ORIGINS``/``MODEL_VERIFICATIONS``), never
    free text. ``files`` must cover EVERY file of the model."""

    origin: str
    verification: str
    catalog_model_id: str | None = None
    source_url: str | None = None
    revision: str | None = None
    license_notice: str | None = None
    files: tuple[SourceFile, ...] = ()


def local_import_provenance(path: str, sha256: str, size_bytes: int) -> ImportProvenance:
    """The provenance of a plain local-file import: one file, no
    verification, no catalog identity -- exactly what migration 0006's
    backfill writes for pre-existing Sprint 5 rows."""
    return ImportProvenance(
        origin="local_import",
        verification="none",
        files=(SourceFile(filename=PurePath(path).name, sha256=sha256, size_bytes=size_bytes),),
    )


def _files_json(files: tuple[SourceFile, ...]) -> str:
    return json.dumps(
        [{"filename": f.filename, "sha256": f.sha256, "size_bytes": f.size_bytes} for f in files]
    )


def _merge_source(source: ModelSource, provenance: ImportProvenance) -> None:
    """Merge a duplicate registration's provenance into the existing
    `model_source` row (module docstring's rules). ``updated_at`` is set
    unconditionally so every merge is observable even when nothing else
    changed."""
    try:
        existing_files = json.loads(source.files_json)
    except ValueError:
        existing_files = []  # malformed: the new list wins
    if not isinstance(existing_files, list):
        existing_files = []
    if len(provenance.files) >= len(existing_files):
        source.files_json = _files_json(provenance.files)
    source.origin = provenance.origin
    if provenance.catalog_model_id is not None:
        source.catalog_model_id = provenance.catalog_model_id
    if provenance.source_url is not None:
        source.source_url = provenance.source_url
    if provenance.revision is not None:
        source.revision = provenance.revision
    if provenance.license_notice is not None:
        source.license_notice = provenance.license_notice
    source.updated_at = utc_now()


def _refresh_existing(existing: ModelFile, path: str, provenance: ImportProvenance) -> None:
    """A duplicate registration: refresh location and verification
    timestamp, and restate the registry distinction on the record."""
    existing.path = path
    existing.last_verified_at = utc_now()
    existing.origin = provenance.origin
    existing.verification = provenance.verification


def persist_model_import(
    database: Database,
    *,
    role: str,
    display_name: str,
    path: str,
    sha256: str,
    size_bytes: int,
    provenance: ImportProvenance | None = None,
) -> tuple[ModelFile, bool]:
    """Persist one imported model file record, duplicate-aware. Returns
    ``(record, duplicate)``; a duplicate has ``last_verified_at``
    refreshed (and, if the file moved, its location updated). The caller
    has already validated and hashed the content; this is persistence
    only -- it never touches the filesystem.

    ``provenance`` defaults to a plain local-file import; catalog and
    URL downloads pass the full registry provenance, which is written in
    the same transaction."""
    if provenance is None:
        provenance = local_import_provenance(path, sha256, size_bytes)
    with database.session(write=True) as session:
        existing = session.scalar(
            select(ModelFile).where(ModelFile.sha256_hash == sha256, ModelFile.role == role)
        )
        if existing is not None:
            _refresh_existing(existing, path, provenance)
            source = session.scalar(
                select(ModelSource).where(ModelSource.model_file_id == existing.id)
            )
            if source is None:
                # Unreachable for databases migrated to 0006 (the backfill
                # is 1:1), but a missing source row must not become a
                # half-registered record: write it now.
                session.add(
                    ModelSource(
                        id=str(uuid.uuid4()),
                        model_file_id=existing.id,
                        origin=provenance.origin,
                        catalog_model_id=provenance.catalog_model_id,
                        source_url=provenance.source_url,
                        revision=provenance.revision,
                        license_notice=provenance.license_notice,
                        files_json=_files_json(provenance.files),
                    )
                )
            else:
                _merge_source(source, provenance)
            session.flush()
            return existing, True
        record = ModelFile(
            id=str(uuid.uuid4()),
            role=role,
            display_name=display_name,
            path=path,
            sha256_hash=sha256,
            size_bytes=size_bytes,
            imported_at=utc_now(),
            origin=provenance.origin,
            verification=provenance.verification,
        )
        session.add(record)
        session.flush()
        session.add(
            ModelSource(
                id=str(uuid.uuid4()),
                model_file_id=record.id,
                origin=provenance.origin,
                catalog_model_id=provenance.catalog_model_id,
                source_url=provenance.source_url,
                revision=provenance.revision,
                license_notice=provenance.license_notice,
                files_json=_files_json(provenance.files),
            )
        )
        session.flush()
        return record, False


def persist_model_import_with_retry(
    database: Database,
    *,
    role: str,
    display_name: str,
    path: str,
    sha256: str,
    size_bytes: int,
    provenance: ImportProvenance | None = None,
) -> tuple[ModelFile, bool]:
    """`persist_model_import` plus the concurrent-import retry: if the
    unique ``(sha256_hash, role)`` constraint decides a race against us,
    re-read the winner and refresh it like a duplicate."""
    try:
        return persist_model_import(
            database,
            role=role,
            display_name=display_name,
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            provenance=provenance,
        )
    except IntegrityError:
        if provenance is None:
            provenance = local_import_provenance(path, sha256, size_bytes)
        with database.session(write=True) as session:
            winner = session.scalar(
                select(ModelFile).where(ModelFile.sha256_hash == sha256, ModelFile.role == role)
            )
            if winner is None:
                raise
            _refresh_existing(winner, path, provenance)
            source = session.scalar(
                select(ModelSource).where(ModelSource.model_file_id == winner.id)
            )
            if source is None:
                session.add(
                    ModelSource(
                        id=str(uuid.uuid4()),
                        model_file_id=winner.id,
                        origin=provenance.origin,
                        catalog_model_id=provenance.catalog_model_id,
                        source_url=provenance.source_url,
                        revision=provenance.revision,
                        license_notice=provenance.license_notice,
                        files_json=_files_json(provenance.files),
                    )
                )
            else:
                _merge_source(source, provenance)
            session.flush()
            return winner, True
