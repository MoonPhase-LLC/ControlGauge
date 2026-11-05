"""Evidence upload and ingestion status (Sprint 4).

    POST /assessments/{id}/artifacts                   raw file bytes as the body;
                                                       name in `X-ControlGauge-Filename`
    GET  /assessments/{id}/artifacts                   per-file status, newest first
    GET  /artifacts/{id}                               one artifact
    GET  /artifacts/{id}/sections                      extracted sections (paged)

Upload checks run in this order, all before anything is stored or parsed:
assessment exists; declared and actual size <= `MAX_FILE_BYTES`
(streamed, never buffered past the limit); filename clean; extension
allowed; content sniff matches the extension; per-assessment count and
byte limits. An exact duplicate (same SHA-256 in the same assessment) is
**flagged** -- the existing artifact is returned with `duplicate: true` and
nothing is stored or parsed again (`USER_FLOWS.md` §5).

The file name travels in a header (percent-encoded UTF-8), not the URL, so
request-line logging anywhere on the path can never record it.

Errors use the fixed contract in `routes/errors.py`; nothing from the
request (filename, content) is echoed in an error.
"""

from __future__ import annotations

import hashlib
import urllib.parse
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..db import Database
from ..db.models import Artifact, ArtifactSection, Assessment
from ..evidence import filetype, limits
from ..evidence.ingest import IngestionService
from .deps import get_database
from .errors import ApiError

router = APIRouter()

LIST_LIMIT_MAX = 200
SECTION_LIMIT_MAX = 200


class ArtifactOut(BaseModel):
    id: str
    assessment_id: str
    filename: str
    sha256: str
    size_bytes: int
    format: str
    mime_type: str
    parse_status: str
    parse_detail: str | None
    parser_version: str | None
    section_count: int
    uploaded_at: datetime
    parsed_at: datetime | None


class UploadResult(BaseModel):
    artifact: ArtifactOut
    duplicate: bool


class UploadLimits(BaseModel):
    max_file_bytes: int
    max_files_per_batch: int
    max_artifacts_per_assessment: int
    max_assessment_bytes: int


class ArtifactList(BaseModel):
    artifacts: list[ArtifactOut]
    total: int
    total_bytes: int
    limit: int
    offset: int
    limits: UploadLimits


class SectionOut(BaseModel):
    id: str
    sequence_index: int
    locator: str
    content: str


class SectionList(BaseModel):
    artifact_id: str
    sections: list[SectionOut]
    total: int
    limit: int
    offset: int


UPLOAD_LIMITS = UploadLimits(
    max_file_bytes=limits.MAX_FILE_BYTES,
    max_files_per_batch=limits.MAX_FILES_PER_BATCH,
    max_artifacts_per_assessment=limits.MAX_ARTIFACTS_PER_ASSESSMENT,
    max_assessment_bytes=limits.MAX_ASSESSMENT_BYTES,
)


def _to_out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id,
        assessment_id=a.assessment_id,
        filename=a.filename,
        sha256=a.sha256_hash,
        size_bytes=a.size_bytes,
        format=a.format,
        mime_type=a.mime_type,
        parse_status=a.parse_status,
        parse_detail=a.parse_detail,
        parser_version=a.parser_version,
        section_count=a.section_count,
        uploaded_at=a.uploaded_at,
        parsed_at=a.parsed_at,
    )


def _canonical(value: str, error: ApiError) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise error from None
    if canonical != value:
        raise error
    return canonical


def _assessment_not_found() -> ApiError:
    return ApiError(404, "assessment_not_found", "The assessment was not found.")


def _artifact_not_found() -> ApiError:
    return ApiError(404, "artifact_not_found", "The artifact was not found.")


_UPLOAD_ERRORS = {
    "invalid_filename": (422, "The file name is not valid."),
    "unsupported_file_type": (415, "Only PDF, DOCX, TXT and CSV files can be uploaded."),
    "file_type_mismatch": (415, "The file content does not match its file type."),
    "file_too_large": (413, "The file is larger than the upload limit."),
    "assessment_artifact_limit": (409, "The assessment has reached its file limit."),
    "assessment_size_limit": (409, "The assessment has reached its storage limit."),
}


def _upload_error(code: str) -> ApiError:
    status, message = _UPLOAD_ERRORS[code]
    return ApiError(status, code, message)


def get_ingestion(request: Request) -> IngestionService:
    ingestion: IngestionService | None = getattr(request.app.state, "ingestion", None)
    if ingestion is None:
        raise ApiError(503, "ingestion_unavailable", "Evidence ingestion is not available.")
    return ingestion


DatabaseDep = Annotated[Database, Depends(get_database)]
IngestionDep = Annotated[IngestionService, Depends(get_ingestion)]


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise ApiError(400, "malformed_body", "The request body is not valid.")
        if int(declared) > limits.MAX_FILE_BYTES:
            raise _upload_error("file_too_large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limits.MAX_FILE_BYTES:
            raise _upload_error("file_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/assessments/{assessment_id}/artifacts")
async def upload_artifact(
    assessment_id: str,
    request: Request,
    database: DatabaseDep,
    ingestion: IngestionDep,
    x_controlgauge_filename: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    canonical = _canonical(assessment_id, _assessment_not_found())
    encoded = x_controlgauge_filename
    if encoded is None or not encoded or len(encoded) > 3 * 1024:
        raise _upload_error("invalid_filename")
    try:
        filename = urllib.parse.unquote(encoded, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _upload_error("invalid_filename") from None

    def exists() -> bool:
        with database.session() as session:
            return session.get(Assessment, canonical) is not None

    if not await run_in_threadpool(exists):
        raise _assessment_not_found()
    try:
        name = filetype.clean_filename(filename)
        fmt = filetype.format_for(name)
    except filetype.UploadRejected as exc:
        raise _upload_error(exc.code) from None

    data = await _read_body(request)
    if not filetype.sniff_matches(fmt, data[:4096]):
        raise _upload_error("file_type_mismatch")
    digest = hashlib.sha256(data).hexdigest()

    def store_and_record() -> tuple[ArtifactOut, bool]:
        with database.session() as session:
            existing = session.scalar(
                select(Artifact).where(
                    Artifact.assessment_id == canonical, Artifact.sha256_hash == digest
                )
            )
            if existing is not None:
                return _to_out(existing), True
            count, used = session.execute(
                select(func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                    Artifact.assessment_id == canonical
                )
            ).one()
        if count >= limits.MAX_ARTIFACTS_PER_ASSESSMENT:
            raise _upload_error("assessment_artifact_limit")
        if used + len(data) > limits.MAX_ASSESSMENT_BYTES:
            raise _upload_error("assessment_size_limit")
        # The file is stored before its row exists, so a row never points
        # at a missing file. Same hash -> same path -> same bytes.
        ingestion.store.save(canonical, digest, data)
        artifact = Artifact(
            id=str(uuid.uuid4()),
            assessment_id=canonical,
            filename=name,
            sha256_hash=digest,
            size_bytes=len(data),
            format=fmt,
            mime_type=filetype.MIME_TYPES[fmt],
            parse_status="pending",
            section_count=0,
        )
        try:
            with database.session(write=True) as session:
                # Re-checked inside the write transaction (BEGIN IMMEDIATE):
                # two concurrent uploads cannot both pass the limits.
                count, used = session.execute(
                    select(func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                        Artifact.assessment_id == canonical
                    )
                ).one()
                if count >= limits.MAX_ARTIFACTS_PER_ASSESSMENT:
                    raise _upload_error("assessment_artifact_limit")
                if used + len(data) > limits.MAX_ASSESSMENT_BYTES:
                    raise _upload_error("assessment_size_limit")
                session.add(artifact)
                session.flush()
                out = _to_out(artifact)
        except IntegrityError:
            # A concurrent upload of the same file won the race.
            with database.session() as session:
                winner = session.scalar(
                    select(Artifact).where(
                        Artifact.assessment_id == canonical, Artifact.sha256_hash == digest
                    )
                )
                if winner is None:
                    raise
                return _to_out(winner), True
        return out, False

    out, duplicate = await run_in_threadpool(store_and_record)
    if not duplicate:
        ingestion.enqueue(out.id)
    body = UploadResult(artifact=out, duplicate=duplicate)
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if duplicate else 201)


@router.get("/assessments/{assessment_id}/artifacts")
def list_artifacts(
    assessment_id: str,
    database: DatabaseDep,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ArtifactList:
    canonical = _canonical(assessment_id, _assessment_not_found())
    with database.session() as session:
        if session.get(Assessment, canonical) is None:
            raise _assessment_not_found()
        total, total_bytes = session.execute(
            select(func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                Artifact.assessment_id == canonical
            )
        ).one()
        rows: Any = session.scalars(
            select(Artifact)
            .where(Artifact.assessment_id == canonical)
            .order_by(Artifact.uploaded_at.desc(), Artifact.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return ArtifactList(
            artifacts=[_to_out(a) for a in rows],
            total=total,
            total_bytes=total_bytes,
            limit=limit,
            offset=offset,
            limits=UPLOAD_LIMITS,
        )


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, database: DatabaseDep) -> ArtifactOut:
    canonical = _canonical(artifact_id, _artifact_not_found())
    with database.session() as session:
        artifact = session.get(Artifact, canonical)
        if artifact is None:
            raise _artifact_not_found()
        return _to_out(artifact)


@router.get("/artifacts/{artifact_id}/sections")
def list_sections(
    artifact_id: str,
    database: DatabaseDep,
    limit: Annotated[int, Query(ge=1, le=SECTION_LIMIT_MAX)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> SectionList:
    canonical = _canonical(artifact_id, _artifact_not_found())
    with database.session() as session:
        artifact = session.get(Artifact, canonical)
        if artifact is None:
            raise _artifact_not_found()
        rows: Any = session.scalars(
            select(ArtifactSection)
            .where(ArtifactSection.artifact_id == canonical)
            .order_by(ArtifactSection.sequence_index)
            .limit(limit)
            .offset(offset)
        ).all()
        return SectionList(
            artifact_id=canonical,
            sections=[
                SectionOut(
                    id=s.id, sequence_index=s.sequence_index, locator=s.locator, content=s.content
                )
                for s in rows
            ],
            total=artifact.section_count,
            limit=limit,
            offset=offset,
        )
