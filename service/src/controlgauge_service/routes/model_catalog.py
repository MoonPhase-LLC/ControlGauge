"""Curated catalog listing (Sprint 6, S6-03).

    GET /model-catalog    the shipped catalog entries + installed state

Structural by contract (global contract 1): the answer is the strictly
loaded in-repo manifest plus one local database read for installed state
-- no model-child contact, no network, no multi-second work. The catalog
is DATA shipped with the app; a manifest that fails to load is 503
``catalog_unavailable`` fail-closed (never partially listed).

``installed`` is computed from ``model_file.sha256_hash`` matches for now
(every declared file's SHA-256 present among imported rows); S6-05 swaps
the lookup to the ``model_source`` registry table -- the response schema
is identical from day one. Responses carry catalog metadata only: no
local path ever appears (T-10).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from ..db import Database
from ..db.models import ModelFile
from ..model_catalog.manifest import CatalogEntry, CatalogManifest, entry_total_bytes, load_catalog
from ..model_runtime.errors import ModelRuntimeError
from .deps import get_database
from .errors import ApiError

logger = logging.getLogger("controlgauge_service.routes.model_catalog")

router = APIRouter()

DatabaseDep = Annotated[Database, Depends(get_database)]


# --- response schemas (closed) ---------------------------------------------------


class CatalogFileOut(BaseModel):
    filename: str
    size_bytes: int
    sha256: str


class CatalogEntryOut(BaseModel):
    model_id: str
    display_name: str
    format: str
    backend: str
    roles: list[str]
    tier: str | None
    recommended: bool
    recommendation_withheld_reason: str | None
    context_window: int
    curated_context_tokens: int
    max_input_tokens: int
    max_output_tokens: int | None
    total_bytes: int
    license: str
    revision: str
    repository_url: str
    embedding_dimensions: int | None
    installed: bool
    installed_model_ids: list[str]
    evaluation_status: str


class CatalogOut(BaseModel):
    catalog_version: str
    entries: list[CatalogEntryOut]


# --- helpers -----------------------------------------------------------------------


def _installed_state(
    database: Database, manifest: CatalogManifest
) -> dict[str, tuple[bool, list[str]]]:
    """Per-entry ``(installed, installed_model_ids)`` from one local
    database read: a row matches an entry when its SHA-256 equals one of
    the entry's declared files; installed means EVERY declared file is
    matched (a partial shard set is not installed)."""
    with database.session() as session:
        rows = session.execute(select(ModelFile.sha256_hash, ModelFile.id)).all()
    ids_by_sha: dict[str, list[str]] = {}
    for sha256_hash, model_id in rows:
        ids_by_sha.setdefault(sha256_hash, []).append(model_id)
    state: dict[str, tuple[bool, list[str]]] = {}
    for entry in manifest.entries:
        required = {entry_file.sha256 for entry_file in entry.files}
        matched: list[str] = []
        for sha256 in required:
            matched.extend(ids_by_sha.get(sha256, []))
        state[entry.model_id] = (required.issubset(ids_by_sha.keys()), sorted(matched))
    return state


def _to_out(
    entry: CatalogEntry, installed: bool, installed_model_ids: list[str]
) -> CatalogEntryOut:
    return CatalogEntryOut(
        model_id=entry.model_id,
        display_name=entry.display_name,
        format=entry.format,
        backend=entry.backend,
        roles=list(entry.roles),
        tier=entry.tier,
        recommended=entry.recommended,
        recommendation_withheld_reason=entry.recommendation_withheld_reason,
        context_window=entry.context_window,
        curated_context_tokens=entry.curated_context_tokens,
        max_input_tokens=entry.max_input_tokens,
        max_output_tokens=entry.max_output_tokens,
        total_bytes=entry_total_bytes(entry),
        license=entry.source.license,
        revision=entry.source.revision,
        repository_url=entry.source.repository_url,
        embedding_dimensions=entry.embedding_dimensions,
        installed=installed,
        installed_model_ids=installed_model_ids,
        evaluation_status=entry.evaluation.status,
    )


# --- routes --------------------------------------------------------------------------


@router.get("/model-catalog")
def list_catalog(database: DatabaseDep) -> CatalogOut:
    """The shipped curated catalog with installed state. Structural: the
    manifest is local data and the installed check is one bounded DB
    read; live checks are explicit POSTs elsewhere."""
    try:
        manifest = load_catalog()
    except ModelRuntimeError as exc:
        # Fail closed: an unreadable or invalid manifest is a fixed 503,
        # never a partial list and never manifest content in an error.
        raise ApiError(503, exc.code, str(exc)) from None
    state = _installed_state(database, manifest)
    return CatalogOut(
        catalog_version=manifest.catalog_version,
        entries=[_to_out(entry, *state[entry.model_id]) for entry in manifest.entries],
    )
