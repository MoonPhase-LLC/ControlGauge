"""Model import and runtime control (Sprint 5, S5-05).

    POST /models/import           import a local GGUF file (untrusted data)
    GET  /models                  imported models + per-role runtime status
    GET  /models/{id}             one imported model
    POST /models/{id}/start       run the model for its role (supervised)
    POST /models/{id}/stop        stop the role (idempotent)
    POST /models/{id}/health      bounded real capability check

An imported GGUF file is untrusted (`SECURITY.md` T-08, T-10): the import
validates the container shape only (magic + version + size bounds,
`model_runtime/gguf.py`), hashes it, and persists one record. Nothing about
the path, the filename, or the file content is ever logged or echoed in an
error, and the machine-local `path` is stored in the database (the one
sanctioned place for it) but never returned by any route -- responses carry
the cleaned display name and structural metadata only. Import never starts
anything: running an imported model is an explicit, separate start action.

Every model carries the fixed T-08 provenance warning: an imported file's
provenance is NOT verified by the app -- distinct from the verified catalog
downloads a later release adds (Sprint 6).

Errors use the fixed contract in `routes/errors.py`; runtime failures
surface the `MODEL_RUNTIME_ERROR_CODES` code with its fixed message.

All handlers are plain ``def``: FastAPI runs them in its worker threadpool
(the `routes/artifacts.py` blocking-work pattern), which is where the
multi-second hashing and the blocking provider calls belong.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse

from ..db import Database
from ..db.base import utc_now
from ..db.models import ModelFile
from ..model_runtime import gguf
from ..model_runtime.config import (
    EMBEDDING_DEFAULT_CONTEXT_TOKENS,
    GENERATION_DEFAULT_CONTEXT_TOKENS,
    GENERATION_DEFAULT_MAX_OUTPUT_TOKENS,
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from ..model_runtime.errors import ModelRuntimeError
from ..model_runtime.manager import ModelRuntimeManager
from ..model_runtime.protocols import HealthStatus, Role
from .deps import get_database, get_model_runtime
from .errors import ApiError

logger = logging.getLogger("controlgauge_service.routes.models")

router = APIRouter()

DatabaseDep = Annotated[Database, Depends(get_database)]
ModelRuntimeDep = Annotated[ModelRuntimeManager, Depends(get_model_runtime)]

#: Longest accepted import path (matches the desktop shell's Rust-side
#: bound; longer than any plausible absolute path on either platform).
MAX_MODEL_PATH_CHARS = 4096
#: How much of an untrusted file the sniff ever reads: magic + version.
_SNIFF_BYTES = 8
#: Streaming hash chunk (1 MiB): bounded memory for a multi-GB file.
_HASH_CHUNK_BYTES = 1024 * 1024

#: Fixed T-08 provenance warning carried on every import response and
#: rendered by the UI on every imported model. Fixed text -- never
#: interpolates the path, filename, or any other request content.
IMPORT_PROVENANCE_WARNING = (
    "This model was imported from a local file. Its provenance has not "
    "been verified by the app -- this is distinct from verified catalog "
    "downloads (coming in a later release). Only run model files from "
    "sources you trust."
)


# --- response schemas (closed) -----------------------------------------------


class ModelFileOut(BaseModel):
    id: str
    role: str
    display_name: str
    sha256: str
    size_bytes: int
    imported_at: datetime
    last_verified_at: datetime | None
    embedding_dimensions: int | None
    embedding_normalization: str | None
    embedding_similarity_metric: str | None
    # `path` is deliberately absent: it is machine-local (contains the OS
    # user name), lives in the database only, and nothing in the UI needs
    # it (T-10: it must never travel to the frontend or logs).


class ImportResult(BaseModel):
    model: ModelFileOut
    duplicate: bool
    provenance: Literal["unverified_import"]
    warning: str


class RuntimeStatusOut(BaseModel):
    """Closed per-role runtime status. Never a pid, port, path, or
    credential."""

    state: str
    detail_code: str | None = None
    checked_at: datetime | None = None
    model_id: str | None = None


class HealthStatusOut(RuntimeStatusOut):
    """The `/models/{id}/health` answer (same closed shape)."""


class RuntimeStatesOut(BaseModel):
    generation: RuntimeStatusOut | None
    embedding: RuntimeStatusOut | None


class ModelInventory(BaseModel):
    models: list[ModelFileOut]
    runtime: RuntimeStatesOut


# --- request schemas (closed, strict) ----------------------------------------


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: StrictStr
    role: Literal["generation", "embedding"]
    display_name: StrictStr | None = None


class StartOptions(BaseModel):
    """Reserved PD-023 acceleration-configuration surface. The Sprint 5 UI
    sends none of these; the bounds are sanity ceilings -- llama-server is
    the final authority, and an out-of-range value fails the load with
    ``start_failed``."""

    model_config = ConfigDict(extra="forbid")

    context_tokens: int | None = Field(default=None, ge=256, le=131_072)
    max_output_tokens: int | None = Field(default=None, ge=1, le=8_192)
    gpu_layers: int | None = Field(default=None, ge=0, le=1_024)
    threads: int | None = Field(default=None, ge=1, le=256)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_options: StartOptions | None = None


# --- error mapping ------------------------------------------------------------

#: Import-path codes -> HTTP status (mirrors `routes/artifacts.py`'s status
#: choices for the analogous evidence cases).
_IMPORT_ERROR_STATUS: dict[str, int] = {
    "model_path_invalid": 422,
    "model_file_not_found": 404,
    "model_file_not_regular": 422,
    "unsupported_file_type": 415,
    "model_file_too_small": 413,
    "model_file_too_large": 413,
    "invalid_gguf_magic": 415,
    "invalid_gguf_version": 415,
}

#: Start/stop-time runtime codes -> HTTP status. ``runtime_binary_missing``
#: is a configuration gap (503, the service cannot run models);
#: ``model_file_missing`` means a persisted record points at a file that is
#: gone (409, conflict with current machine state); ``not_loaded`` is a
#: stale runtime reference (409); every other runtime failure is a
#: supervised-child failure (502) carrying its fixed detail code.
_START_ERROR_STATUS: dict[str, int] = {
    "runtime_binary_missing": 503,
    "model_file_missing": 409,
    "not_loaded": 409,
}
_CHILD_FAILURE_STATUS = 502


def _import_error(exc: ModelRuntimeError) -> ApiError:
    return ApiError(_IMPORT_ERROR_STATUS[exc.code], exc.code, str(exc))


def _runtime_error(exc: ModelRuntimeError) -> ApiError:
    status = _START_ERROR_STATUS.get(exc.code, _CHILD_FAILURE_STATUS)
    return ApiError(status, exc.code, str(exc))


def _validation_field_error(field: str) -> ApiError:
    return ApiError(
        422,
        "validation_failed",
        "The request is not valid.",
        fields=[{"field": field, "code": "invalid", "message": "This value is not valid."}],
    )


def _model_not_found() -> ApiError:
    return ApiError(404, "model_not_found", "The model was not found.")


def _canonical_model_id(value: str) -> str:
    """Only a canonical lowercase UUID reaches the database or a path
    segment (mirrors `routes/artifacts.py`, but an unknown *shape* is a
    validation error per the S5-05 contract, not a 404)."""
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise _validation_field_error("model_id") from None
    if canonical != value:
        raise _validation_field_error("model_id")
    return canonical


# --- display-name cleaning (local, generic helper) -----------------------------

#: Characters refused in display names (Windows-forbidden plus control and
#: line-separator characters) -- the same rule as
#: `evidence/filetype.clean_filename`, applied through a local helper so the
#: model routes stay independent of the evidence subsystem.
_DISALLOWED_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f<>:\"/\\|?*   ]")

DISPLAY_NAME_MAX = 255


def _clean_display_name(raw: str) -> str:
    """Cleaned display text for a model, from the file's last path segment
    (or a caller-supplied name). Applies the `filetype.clean_filename`
    rules: NFC normalization, no directory part, no blank/``.``/``..``/
    disallowed characters, bounded length. The result is display text only
    and is never used as a path (T-05)."""
    name = unicodedata.normalize("NFC", raw).strip()
    name = re.split(r"[\\/]", name)[-1].strip()
    if not name or name in {".", ".."}:
        raise ModelRuntimeError("model_path_invalid")
    if len(name) > DISPLAY_NAME_MAX or _DISALLOWED_NAME_CHARS.search(name):
        raise ModelRuntimeError("model_path_invalid")
    return name


# --- helpers -------------------------------------------------------------------


def _to_out(record: ModelFile) -> ModelFileOut:
    return ModelFileOut(
        id=record.id,
        role=record.role,
        display_name=record.display_name,
        sha256=record.sha256_hash,
        size_bytes=record.size_bytes,
        imported_at=record.imported_at,
        last_verified_at=record.last_verified_at,
        embedding_dimensions=record.embedding_dimensions,
        embedding_normalization=record.embedding_normalization,
        embedding_similarity_metric=record.embedding_similarity_metric,
    )


def _check_import_path_shape(path_text: str) -> Path:
    """Closed shape checks on the caller-supplied path: non-empty, control-
    character free, length-bounded, absolute. Nothing about the value is
    echoed back (the fixed code is all a client learns)."""
    if not path_text or len(path_text) > MAX_MODEL_PATH_CHARS:
        raise ModelRuntimeError("model_path_invalid")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path_text):
        raise ModelRuntimeError("model_path_invalid")
    path = Path(path_text)
    if not path.is_absolute():
        raise ModelRuntimeError("model_path_invalid")
    return path


def _read_head_and_size(path: Path) -> tuple[bytes, int]:
    """stat + read the first sniff bytes of an untrusted file. Every I/O
    failure maps to the fixed not-found code; nothing about the file is
    logged."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError:
        raise ModelRuntimeError("model_file_not_found") from None
    return head, size


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_status(manager: ModelRuntimeManager, role: Role) -> RuntimeStatusOut:
    status: HealthStatus = manager.status(role)
    return RuntimeStatusOut(
        state=status.state,
        detail_code=status.detail_code,
        checked_at=status.checked_at,
        model_id=manager.loaded_model_id(role),
    )


def _persist_learned_dimension(database: Database, model_id: str, dimensions: int) -> None:
    """The manager's learn-and-pin callback target (S5-04 -> S5-05): the
    first successful embed pins the observed dimensionality on the record,
    where it survives restarts and feeds ``expected_dimensions`` on every
    later start. Runs in the calling worker thread; opens its own session."""
    with database.session(write=True) as session:
        record = session.get(ModelFile, model_id)
        if record is not None:
            record.embedding_dimensions = dimensions


def _build_config(
    record: ModelFile, options: StartOptions | None
) -> GenerationServerConfig | EmbeddingServerConfig:
    opts = options if options is not None else StartOptions()
    path = Path(record.path)
    if record.role == "generation":
        return GenerationServerConfig(
            model_path=path,
            model_id=record.id,
            context_tokens=(
                GENERATION_DEFAULT_CONTEXT_TOKENS
                if opts.context_tokens is None
                else opts.context_tokens
            ),
            max_output_tokens=(
                GENERATION_DEFAULT_MAX_OUTPUT_TOKENS
                if opts.max_output_tokens is None
                else opts.max_output_tokens
            ),
            gpu_layers=opts.gpu_layers,
            threads=opts.threads,
        )
    if opts.max_output_tokens is not None:
        # A reserved field with no meaning for the embedding capability --
        # refuse rather than silently ignore it.
        raise _validation_field_error("start_options.max_output_tokens")
    return EmbeddingServerConfig(
        model_path=path,
        model_id=record.id,
        context_tokens=(
            EMBEDDING_DEFAULT_CONTEXT_TOKENS if opts.context_tokens is None else opts.context_tokens
        ),
        gpu_layers=opts.gpu_layers,
        threads=opts.threads,
        # Drift against the pinned dimension is refused inside embed()
        # (S5-04); None until the first successful embed pins it.
        expected_dimensions=record.embedding_dimensions,
    )


def _get_record(database: Database, canonical: str) -> ModelFile:
    with database.session() as session:
        record = session.get(ModelFile, canonical)
    if record is None:
        raise _model_not_found()
    return record


# --- routes ---------------------------------------------------------------------


@router.post("/models/import")
def import_model(payload: ImportRequest, database: DatabaseDep) -> JSONResponse:
    """Import a user-provided GGUF path: validate the untrusted container
    shape (never parse further, never execute), hash it, persist one
    record. Validation order mirrors `routes/artifacts.py`."""
    try:
        path = _check_import_path_shape(payload.path)
    except ModelRuntimeError as exc:
        raise _import_error(exc) from None

    if path.suffix.lower() != ".gguf":
        raise _import_error(ModelRuntimeError("unsupported_file_type"))

    try:
        display_name = (
            _clean_display_name(payload.display_name)
            if payload.display_name is not None
            else _clean_display_name(path.name)
        )
    except ModelRuntimeError as exc:
        # A caller-supplied name that cannot be cleaned is a request
        # problem; an auto-derived one that cannot be cleaned means the
        # file's final segment is not safely displayable (a legal-but-
        # hostile local filename -- pass `display_name` explicitly).
        # Either way the fixed code refuses the import without echoing
        # the name (T-10).
        raise _import_error(exc) from None

    try:
        head, size = _read_head_and_size(path)
        gguf.validate_gguf_head(head, size)
    except ModelRuntimeError as exc:
        raise _import_error(exc) from None

    digest = _stream_sha256(path)
    role: Role = payload.role

    def persist() -> tuple[ModelFileOut, bool]:
        with database.session(write=True) as session:
            existing = session.scalar(
                select(ModelFile).where(ModelFile.sha256_hash == digest, ModelFile.role == role)
            )
            if existing is not None:
                # Same content, same role: refresh the verification
                # timestamp and (if the file moved) its location.
                existing.path = str(path)
                existing.last_verified_at = utc_now()
                return _to_out(existing), True
            record = ModelFile(
                id=str(uuid.uuid4()),
                role=role,
                display_name=display_name,
                path=str(path),
                sha256_hash=digest,
                size_bytes=size,
                imported_at=utc_now(),
            )
            session.add(record)
            session.flush()
            return _to_out(record), False

    try:
        out, duplicate = persist()
    except IntegrityError:
        # A concurrent import of the same file won the race (unique
        # sha256+role): refresh the winner like a duplicate.
        with database.session(write=True) as session:
            winner = session.scalar(
                select(ModelFile).where(ModelFile.sha256_hash == digest, ModelFile.role == role)
            )
            if winner is None:
                raise
            winner.path = str(path)
            winner.last_verified_at = utc_now()
            session.flush()
            out, duplicate = _to_out(winner), True

    # Structural outcome only -- never the path, name, or content (T-10).
    logger.info(
        "model_imported",
        extra={
            "model_id": out.id,
            "role": out.role,
            "size_bytes": out.size_bytes,
            "duplicate": duplicate,
        },
    )
    body = ImportResult(
        model=out,
        duplicate=duplicate,
        provenance="unverified_import",
        warning=IMPORT_PROVENANCE_WARNING,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if duplicate else 201)


@router.get("/models")
def list_models(
    database: DatabaseDep,
    manager: ModelRuntimeDep,
) -> ModelInventory:
    """Every imported model plus the live per-role runtime status (a real
    capability check per occupied role, D-023 -- closed fields only)."""
    with database.session() as session:
        rows: Any = session.scalars(
            select(ModelFile).order_by(ModelFile.imported_at.desc(), ModelFile.id.desc())
        ).all()
        models = [_to_out(record) for record in rows]
    return ModelInventory(
        models=models,
        runtime=RuntimeStatesOut(
            generation=_runtime_status(manager, "generation"),
            embedding=_runtime_status(manager, "embedding"),
        ),
    )


@router.get("/models/{model_id}")
def get_model(model_id: str, database: DatabaseDep) -> ModelFileOut:
    canonical = _canonical_model_id(model_id)
    return _to_out(_get_record(database, canonical))


@router.post("/models/{model_id}/start")
def start_model(
    model_id: str,
    payload: StartRequest,
    database: DatabaseDep,
    manager: ModelRuntimeDep,
) -> RuntimeStatusOut:
    """Run an imported model for its role. Starting over an occupied role
    is the documented switch (S5-02); re-starting the already-running model
    goes through the same stop-then-start (the manager's contract)."""
    canonical = _canonical_model_id(model_id)
    record = _get_record(database, canonical)
    config = _build_config(record, payload.start_options)

    try:
        manager.start(
            record.role,  # type: ignore[arg-type]
            config,
            display_name=record.display_name,
            on_dimension_learned=(
                (lambda dimensions: _persist_learned_dimension(database, canonical, dimensions))
                if record.role == "embedding"
                else None
            ),
        )
    except ModelRuntimeError as exc:
        raise _runtime_error(exc) from None
    logger.info("model_started", extra={"model_id": canonical, "role": record.role})
    return _runtime_status(manager, record.role)  # type: ignore[arg-type]


@router.post("/models/{model_id}/stop")
def stop_model(
    model_id: str,
    database: DatabaseDep,
    manager: ModelRuntimeDep,
) -> RuntimeStatusOut:
    """Idempotent stop of the model's role (the role's current occupant,
    whichever record started it)."""
    canonical = _canonical_model_id(model_id)
    record = _get_record(database, canonical)
    manager.stop(record.role)  # type: ignore[arg-type]
    logger.info("model_stopped", extra={"model_id": canonical, "role": record.role})
    return RuntimeStatusOut(state="stopped", detail_code=None, checked_at=None, model_id=None)


@router.post("/models/{model_id}/health")
def health_model(
    model_id: str,
    database: DatabaseDep,
    manager: ModelRuntimeDep,
) -> HealthStatusOut:
    """Bounded real capability check for the model. ``unhealthy`` (and
    ``stopped``) are statuses, not HTTP errors; a model that is not the
    role's current occupant reports ``stopped`` without being contacted."""
    canonical = _canonical_model_id(model_id)
    record = _get_record(database, canonical)
    role: Role = record.role  # type: ignore[assignment]
    if manager.loaded_model_id(role) != canonical:
        return HealthStatusOut(state="stopped", detail_code=None, checked_at=None, model_id=None)
    status = manager.status(role)
    return HealthStatusOut(
        state=status.state,
        detail_code=status.detail_code,
        checked_at=status.checked_at,
        model_id=canonical,
    )
