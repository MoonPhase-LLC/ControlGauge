"""Curated catalog listing (Sprint 6, S6-03).

    GET /model-catalog    the shipped catalog entries + installed state

Structural by contract (global contract 1): the answer is the strictly
loaded in-repo manifest plus one local database read for installed state
-- no model-child contact, no network, no multi-second work. The catalog
is DATA shipped with the app; a manifest that fails to load is 503
``catalog_unavailable`` fail-closed (never partially listed).

``installed`` is computed from the ``model_source`` registry (S6-05):
content identity is the SHA-256, and an entry is installed when every
declared digest is covered across registration rows -- one multi-shard
registration whose ``files_json`` spans both shards, or legacy per-shard
rows (a partial shard set is not installed). A malformed ``files_json``
fails closed to "does not cover". Responses carry catalog metadata only:
no local path ever appears (T-10).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import Request as FastApiRequest
from pydantic import BaseModel
from sqlalchemy import select
from starlette.responses import JSONResponse

from ..config import resolve_model_dir
from ..db import Database
from ..db.models import ModelFile, ModelSource
from ..model_catalog.manifest import (
    CatalogEntry,
    CatalogManifest,
    entry_total_bytes,
    load_catalog,
)
from ..model_runtime.download import (
    DownloadFileSpec,
    DownloadManager,
    DownloadRequest,
    DownloadStatus,
    new_download_id,
    validate_catalog_urls,
)
from ..model_runtime.errors import ModelRuntimeError
from .deps import get_database, get_downloads
from .errors import ApiError

logger = logging.getLogger("controlgauge_service.routes.model_catalog")

router = APIRouter()

DatabaseDep = Annotated[Database, Depends(get_database)]
DownloadsDep = Annotated[DownloadManager, Depends(get_downloads)]


# --- response schemas (closed) ---------------------------------------------------


class CatalogFileOut(BaseModel):
    filename: str
    size_bytes: int
    sha256: str


class CatalogFileOut(BaseModel):
    """One declared file. The SHA-256 is a computed content identity
    (allowed structural metadata, T-10) and the filename is the REMOTE
    artifact name -- never a local path."""

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
    #: The declared per-file details the download approval dialog shows
    #: (S6-07): the user approves exactly these files and digests.
    files: list[CatalogFileOut]


class CatalogOut(BaseModel):
    catalog_version: str
    entries: list[CatalogEntryOut]


class DownloadStartedOut(BaseModel):
    """The 202 body of an approved download start: the poll id only."""

    download_id: str


class DownloadFileProgressOut(BaseModel):
    """One file's byte counts. The filename is the REMOTE artifact name
    from the manifest (catalog data, like `CatalogFileOut`) -- never a
    local path (T-10)."""

    filename: str
    bytes_downloaded: int
    bytes_total: int  # 0 = size not declared up front


class DownloadStatusOut(BaseModel):
    """Structural poll answer: state machine + byte counts. Never a path,
    URL, or credential."""

    download_id: str
    state: str
    files: list[DownloadFileProgressOut]
    error_code: str | None


# --- helpers -----------------------------------------------------------------------


def _installed_state(
    database: Database, manifest: CatalogManifest
) -> dict[str, tuple[bool, list[str]]]:
    """Per-entry ``(installed, installed_model_ids)`` from one local
    database read of the `model_source` registry (S6-05). Content
    identity is the SHA-256, exactly as in Sprint 5: a registration row
    covers a declared file when its ``files_json`` names that digest
    (the row's own file is always among them -- every write path and the
    0006 backfill guarantee it). ``installed`` requires EVERY declared
    digest covered across rows: one multi-shard registration whose
    ``files_json`` spans both shards installs the entry, and so do two
    legacy per-shard rows. A partial shard set lists its row but is not
    installed. A malformed ``files_json`` fails closed to "covers
    nothing"; ``catalog_model_id`` is provenance metadata, never the
    matching key (pre-catalog imports carry ``None``)."""
    with database.session() as session:
        rows = session.execute(
            select(ModelSource.files_json, ModelFile.sha256_hash, ModelFile.id).join(
                ModelFile, ModelSource.model_file_id == ModelFile.id
            )
        ).all()
    state: dict[str, tuple[bool, list[str]]] = {}
    for entry in manifest.entries:
        required = {entry_file.sha256 for entry_file in entry.files}
        matched: list[str] = []
        covered_union: set[str] = set()
        for files_json, own_sha, model_id in rows:
            covered = {own_sha}
            try:
                files = json.loads(files_json)
            except ValueError:
                files = None  # malformed provenance fails closed
            if isinstance(files, list):
                covered.update(
                    item.get("sha256")
                    for item in files
                    if isinstance(item, dict) and isinstance(item.get("sha256"), str)
                )
            overlap = covered & required
            if overlap:
                matched.append(model_id)
                covered_union.update(overlap)
        state[entry.model_id] = (required <= covered_union, sorted(matched))
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
        files=[
            CatalogFileOut(
                filename=entry_file.filename,
                size_bytes=entry_file.size_bytes,
                sha256=entry_file.sha256,
            )
            for entry_file in entry.files
        ],
    )


# --- routes --------------------------------------------------------------------------

#: Download/catalog codes -> HTTP status (alongside `_IMPORT_ERROR_STATUS`
#: in `routes/models.py`; 502 transport / 507 storage follow the artifacts
#: router's analogous choices).
_DOWNLOAD_ERROR_STATUS: dict[str, int] = {
    "catalog_entry_unknown": 404,
    "catalog_unavailable": 503,
    "download_active": 409,
    "download_not_found": 404,
    "download_url_unsupported": 422,
    "download_checksum_mismatch": 422,
    "download_size_mismatch": 422,
    "download_network_error": 502,
    "download_http_error": 502,
    "disk_write_failed": 507,
    "disk_space_insufficient": 507,
}


def _download_error(exc: ModelRuntimeError) -> ApiError:
    return ApiError(_DOWNLOAD_ERROR_STATUS.get(exc.code, 422), exc.code, str(exc))


def _load_manifest_or_503() -> CatalogManifest:
    try:
        return load_catalog()
    except ModelRuntimeError as exc:
        # Fail closed: an unreadable or invalid manifest is a fixed 503,
        # never a partial list and never manifest content in an error.
        raise ApiError(503, exc.code, str(exc)) from None


@router.get("/model-catalog")
def list_catalog(database: DatabaseDep) -> CatalogOut:
    """The shipped curated catalog with installed state. Structural: the
    manifest is local data and the installed check is one bounded DB
    read; live checks are explicit POSTs elsewhere."""
    manifest = _load_manifest_or_503()
    state = _installed_state(database, manifest)
    return CatalogOut(
        catalog_version=manifest.catalog_version,
        entries=[_to_out(entry, *state[entry.model_id]) for entry in manifest.entries],
    )


def _status_out(status: DownloadStatus) -> DownloadStatusOut:
    return DownloadStatusOut(
        download_id=status.download_id,
        state=status.state,
        files=[
            DownloadFileProgressOut(
                filename=entry_file.filename,
                bytes_downloaded=entry_file.bytes_downloaded,
                bytes_total=entry_file.bytes_total,
            )
            for entry_file in status.files
        ],
        error_code=status.error_code,
    )


@router.post("/model-catalog/downloads/{download_id}/cancel")
def cancel_download(download_id: str, downloads: DownloadsDep) -> DownloadStatusOut:
    """Request cancellation of a running download. The worker stops at
    the next chunk boundary and retains the ``.part`` (a later attempt
    resumes); cancelling an unknown id is 404, cancelling a terminal
    download is a no-op returning its status."""
    try:
        return _status_out(downloads.cancel(download_id))
    except ModelRuntimeError as exc:
        raise _download_error(exc) from None


@router.get("/model-catalog/downloads/{download_id}")
def download_status(download_id: str, downloads: DownloadsDep) -> DownloadStatusOut:
    """Structural poll of a download: in-memory snapshot, no child
    contact, no network, no paths -- state + filenames + byte counts
    only."""
    try:
        return _status_out(downloads.status(download_id))
    except ModelRuntimeError as exc:
        raise _download_error(exc) from None


@router.post("/model-catalog/{model_id}/download")
def start_catalog_download(
    model_id: str, request: FastApiRequest, downloads: DownloadsDep
) -> JSONResponse:
    """THE approval action (S6-04): downloading a catalog entry happens
    only behind this authenticated POST -- the UI confirms first, the
    service treats the POST itself as the user's approval. Everything
    before the thread starts is bounded local work (manifest load + URL
    validation + disk path resolution); the transfer itself is polled
    structurally."""
    manifest = _load_manifest_or_503()
    entry = next(
        (candidate for candidate in manifest.entries if candidate.model_id == model_id), None
    )
    if entry is None:
        raise ApiError(
            404, "catalog_entry_unknown", str(ModelRuntimeError("catalog_entry_unknown"))
        ) from None
    try:
        validate_catalog_urls(entry.files)
    except ModelRuntimeError as exc:
        raise _download_error(exc) from None

    settings = request.app.state.settings
    download_request = DownloadRequest(
        download_id=new_download_id(),
        display_name=entry.display_name,
        role=entry.roles[0],
        dest_dir=resolve_model_dir(settings),
        files=tuple(
            DownloadFileSpec(
                filename=entry_file.filename,
                url=entry_file.download_url,
                size_bytes=entry_file.size_bytes,
                sha256=entry_file.sha256,
            )
            for entry_file in entry.files
        ),
        origin="catalog_download",
        catalog_model_id=entry.model_id,
        source_url=entry.source.repository_url,
        revision=entry.source.revision,
        license_notice=entry.source.license,
        verification="catalog_manifest",
    )
    try:
        download_id = downloads.start(download_request)
    except ModelRuntimeError as exc:
        raise _download_error(exc) from None
    logger.info(
        "catalog_download_started",
        extra={"download_id": download_id, "catalog_model_id": entry.model_id},
    )
    return JSONResponse(
        DownloadStartedOut(download_id=download_id).model_dump(mode="json"), status_code=202
    )
