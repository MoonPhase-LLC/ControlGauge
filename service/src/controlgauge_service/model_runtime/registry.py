"""Shared model-import persistence (S5-05 logic, extracted S6-04).

The one transactional writer for `model_file` rows: duplicate-aware
(same content + same role refreshes the existing record's verification
timestamp and location instead of inserting), race-safe (the unique
``(sha256_hash, role)`` constraint decides; a lost race re-reads the
winner), and T-10 clean (nothing about the path or filename is logged).

S6-04 extracts it verbatim from the S5-05 import route so the download
manager registers through the EXACT same path ("registration through the
verified-import path"): one transaction, one row, same duplicate
semantics. S6-05 extends this module with the `model_source` registry
(migration 0006) -- until then `verification` is an in-memory concept
only (the response's ``provenance`` field is its display channel); no
verification column exists yet and none is written here.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..db.base import utc_now
from ..db.models import ModelFile

logger = logging.getLogger("controlgauge_service.model_runtime.registry")


def persist_model_import(
    database: Database,
    *,
    role: str,
    display_name: str,
    path: str,
    sha256: str,
    size_bytes: int,
) -> tuple[ModelFile, bool]:
    """Persist one imported model file record, duplicate-aware. Returns
    ``(record, duplicate)``; a duplicate has ``last_verified_at``
    refreshed (and, if the file moved, its location updated). The caller
    has already validated and hashed the content; this is persistence
    only -- it never touches the filesystem."""
    with database.session(write=True) as session:
        existing = session.scalar(
            select(ModelFile).where(ModelFile.sha256_hash == sha256, ModelFile.role == role)
        )
        if existing is not None:
            # Same content, same role: refresh the verification timestamp
            # and (if the file moved) its location.
            existing.path = path
            existing.last_verified_at = utc_now()
            return existing, True
        record = ModelFile(
            id=str(uuid.uuid4()),
            role=role,
            display_name=display_name,
            path=path,
            sha256_hash=sha256,
            size_bytes=size_bytes,
            imported_at=utc_now(),
        )
        session.add(record)
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
        )
    except IntegrityError:
        with database.session(write=True) as session:
            winner = session.scalar(
                select(ModelFile).where(ModelFile.sha256_hash == sha256, ModelFile.role == role)
            )
            if winner is None:
                raise
            winner.path = path
            winner.last_verified_at = utc_now()
            session.flush()
            return winner, True
