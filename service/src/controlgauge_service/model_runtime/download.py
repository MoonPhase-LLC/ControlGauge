"""User-approved, resumable, checksum-fail-closed downloads (S6-04).

The mechanics:

- stream to ``<dest>/<filename>.part`` with an incremental SHA-256;
- resume: an existing ``.part`` smaller than the expected size sends
  ``Range: bytes=<n>-`` and the digest is resumed by pre-hashing the
  part's bytes; a server that answers 200 instead of 206 restarts the
  stream from zero;
- fail-closed verification BEFORE anything is registered or renamed:
  exact size first (`download_size_mismatch`), then the digest against
  ``spec.sha256`` when the spec declares one (`download_checksum_mismatch`);
  a mismatch deletes the part -- a corrupt partial download can never
  poison later resumes. Cancel and network failures RETAIN the part
  (that is what makes a later attempt resumable);
- the final file appears only via ``os.replace`` after verification.

Transport discipline (PD-026): https-only for catalog URLs, no downgrade
(any redirect to an ``http://`` target is refused), ``trust_env=False``
(ambient proxy variables can never route a "direct" download elsewhere),
zero transport retries, GET only, and no user data in any request -- the
URL comes from the shipped manifest or the user's own import request.
The integrity control is the checksum; the transport control is
https-with-no-downgrade. There is deliberately no host allowlist: HF CDN
redirect targets churn (``cas-bridge.xethub.hf.co`` today), and a stale
allowlist would break real downloads while adding nothing the checksum
does not already guarantee.

One download at a time (V0.1, no queue): `DownloadManager.start` refuses
with ``download_active``. The download record is in-memory; after a
restart an orphaned ``.part`` is simply picked up by the next download
attempt of the same file (the resume path needs nothing else).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from ..db import Database
from . import gguf
from .config import MIN_MODEL_FILE_BYTES
from .errors import ModelRuntimeError
from .registry import ImportProvenance, SourceFile, persist_model_import_with_retry

if TYPE_CHECKING:
    from ..model_catalog.manifest import CatalogFileSpec

logger = logging.getLogger("controlgauge_service.model_runtime.download")

#: Redirect cap (httpx ``max_redirects``); the 6th hop fails closed.
MAX_REDIRECTS = 5
#: Streaming hash + write chunk: bounded memory for a multi-GB file.
HASH_CHUNK_BYTES = 1024 * 1024
#: The in-progress suffix; removed by the final verified rename.
PART_SUFFIX = ".part"
#: Hosts an ``http://`` URL may target at all (loopback only -- the
#: test-fixture and local-development case). Everything else must be
#: https. Redirect targets are held to a stricter rule: https only.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
#: How much of a downloaded file the registration sniff reads.
_SNIFF_BYTES = 8
#: Terminal states.
_STATE_PENDING = "pending"
_STATE_DOWNLOADING = "downloading"
_STATE_VERIFYING = "verifying"
_STATE_COMPLETED = "completed"
_STATE_FAILED = "failed"
_STATE_CANCELLED = "cancelled"
_TERMINAL_STATES = frozenset({_STATE_COMPLETED, _STATE_FAILED, _STATE_CANCELLED})
#: How many terminal download records are kept for polling.
_MAX_RECORDS = 32


class _DownloadCancelled(Exception):
    """Internal control-flow signal; never leaves this module."""


@dataclass(frozen=True)
class DownloadFileSpec:
    """One file to download. ``size_bytes == 0`` means the size is
    unknown up front (direct URL imports) -- the exact-size gate is
    skipped and the computed digest becomes the record's identity.
    ``sha256 is None`` means record-only (an unverified URL import the
    user explicitly acknowledged)."""

    filename: str
    url: str
    size_bytes: int
    sha256: str | None


@dataclass(frozen=True)
class DownloadRequest:
    """One approved download. ``origin``/``verification`` are fixed
    vocabularies, not free text; ``catalog_model_id``/``source_url``/
    ``revision``/``license_notice`` are provenance for the S6-05 registry
    and never logged (a URL must not travel in logs, T-10)."""

    download_id: str
    display_name: str
    role: str
    dest_dir: Path
    files: tuple[DownloadFileSpec, ...]
    origin: str  # "catalog_download" | "url_import"
    catalog_model_id: str | None
    source_url: str | None
    revision: str | None
    license_notice: str | None
    verification: str  # "catalog_manifest" | "user_checksum" | "none"


@dataclass(frozen=True)
class DownloadFileProgress:
    filename: str
    bytes_downloaded: int
    bytes_total: int  # 0 = unknown (size not declared up front)


@dataclass(frozen=True)
class DownloadStatus:
    download_id: str
    state: str  # pending|downloading|verifying|completed|failed|cancelled
    files: tuple[DownloadFileProgress, ...]
    error_code: str | None


@dataclass
class _Record:
    """Mutable per-download bookkeeping (never leaves the manager; polls
    get a frozen `DownloadStatus` snapshot)."""

    request: DownloadRequest
    state: str = _STATE_PENDING
    files: list[DownloadFileProgress] = field(default_factory=list)
    error_code: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class _Registrar(Protocol):
    """The registration seam (test doubles replace it; production is
    `registry.persist_model_import_with_retry`)."""

    def __call__(
        self,
        database: Database,
        *,
        role: str,
        display_name: str,
        path: str,
        sha256: str,
        size_bytes: int,
        provenance: ImportProvenance | None = None,
    ) -> tuple[object, bool]: ...


# --- URL rules -------------------------------------------------------------------


def validate_user_url(url: str) -> None:
    """The user-supplied URL rule: https, or http to a loopback host
    (the local-test/local-development case). Anything else -- including
    every non-loopback plaintext URL -- is refused fail-closed. The URL
    is never echoed back (T-10: the fixed code is all a client learns)."""
    parts = httpx.URL(url)
    scheme = parts.scheme
    host = (parts.host or "").lower()
    if scheme == "https" and host:
        return
    if scheme == "http" and host in _LOOPBACK_HOSTS:
        return
    raise ModelRuntimeError("download_url_unsupported")


def validate_catalog_urls(entry_files: Iterable[CatalogFileSpec]) -> None:
    """Catalog download URLs are https, full stop (the manifest loader
    already enforces the pinned shape; this is the download-time
    fail-closed restatement, so a future loader regression cannot become
    a plaintext download)."""
    for entry_file in entry_files:
        if httpx.URL(entry_file.download_url).scheme != "https":
            raise ModelRuntimeError("download_url_unsupported")


# --- one file --------------------------------------------------------------------


def _refuse_http_redirect(initial_url: str) -> Callable[[httpx.Response], None]:
    """Response event hook: httpx dispatches it for EVERY response,
    including each redirect hop (`Client._send_handling_redirects`), so
    refusing any non-initial ``http://`` response refuses every downgrade
    redirect. The initial response is exempt only when the initial URL
    was itself loopback-http (`validate_user_url`'s allowance); a
    redirect to http is refused regardless of where it came from."""

    def hook(response: httpx.Response) -> None:
        if str(response.request.url) != initial_url and response.request.url.scheme == "http":
            raise ModelRuntimeError("download_url_unsupported")

    return hook


def download_file(
    spec: DownloadFileSpec,
    dest_dir: Path,
    cancel: threading.Event,
    progress: Callable[[int], None],
) -> tuple[Path, str]:
    """Download one file to ``dest_dir`` with resume + fail-closed
    verification. Returns ``(final_path, sha256_hex)`` -- the final path
    exists only after verification passed. Raises ``ModelRuntimeError``
    with a fixed code on every failure; a verification mismatch deletes
    the part, cancel/network failures retain it (resumable)."""
    if cancel.is_set():
        raise _DownloadCancelled()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / spec.filename
    part_path = dest_dir / (spec.filename + PART_SUFFIX)

    existing = part_path.stat().st_size if part_path.is_file() else 0
    if existing and spec.size_bytes and existing >= spec.size_bytes:
        # An oversized or complete-but-unverified part is garbage: start
        # over rather than send a pointless Range.
        part_path.unlink()
        existing = 0
    # Disk-space pre-flight (the `shutil.disk_usage` seam is the test
    # injection point) -- before any request, so an insufficient disk
    # consumes nothing. A declared size gates exactly its remaining
    # bytes; an unknown size (direct URL import) still requires enough
    # free space for the smallest registrable model file.
    if spec.size_bytes:
        needed = spec.size_bytes - existing
    else:
        needed = MIN_MODEL_FILE_BYTES
    if shutil.disk_usage(dest_dir).free < needed:
        raise ModelRuntimeError("disk_space_insufficient")

    hasher = hashlib.sha256()
    if existing:
        with part_path.open("rb") as existing_part:
            while chunk := existing_part.read(HASH_CHUNK_BYTES):
                hasher.update(chunk)
    headers = {"Range": f"bytes={existing}-"} if existing else {}

    initial_url = spec.url
    written = 0
    try:
        with httpx.Client(
            trust_env=False,
            transport=httpx.HTTPTransport(retries=0),
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            event_hooks={"response": [_refuse_http_redirect(initial_url)]},
        ) as client:
            with client.stream("GET", initial_url, headers=headers) as response:
                if existing and response.status_code == 200:
                    # The server ignored Range: restart from zero (the
                    # body starts at byte 0 and the part is truncated).
                    hasher = hashlib.sha256()
                    existing = 0
                if response.status_code not in (200, 206):
                    raise ModelRuntimeError("download_http_error")
                with part_path.open("ab" if existing else "wb") as part:
                    for chunk in response.iter_bytes(chunk_size=HASH_CHUNK_BYTES):
                        if cancel.is_set():
                            raise _DownloadCancelled()
                        part.write(chunk)
                        hasher.update(chunk)
                        written += len(chunk)
                        progress(existing + written)
    except _DownloadCancelled:
        raise  # the part stays: a later attempt resumes from it
    except ModelRuntimeError:
        raise  # hook refusal / http status; the part stays (resumable)
    except httpx.TooManyRedirects:
        raise ModelRuntimeError("download_url_unsupported") from None
    except (httpx.TransportError, httpx.DecodingError):
        raise ModelRuntimeError("download_network_error") from None
    except OSError:
        raise ModelRuntimeError("disk_write_failed") from None

    total = existing + written
    if spec.size_bytes and total != spec.size_bytes:
        # Truncated or padded stream: the content is untrustworthy, so
        # the part is deleted (a resume of corrupt bytes would only fail
        # the digest again).
        part_path.unlink(missing_ok=True)
        raise ModelRuntimeError("download_size_mismatch")
    digest = hasher.hexdigest()
    if spec.sha256 is not None and digest != spec.sha256:
        part_path.unlink(missing_ok=True)
        raise ModelRuntimeError("download_checksum_mismatch")
    os.replace(part_path, final_path)
    return final_path, digest


# --- the manager -------------------------------------------------------------------


class DownloadManager:
    """One approved download at a time, on a daemon thread, registered
    through the verified-import path on full success."""

    def __init__(self, database: Database, *, registrar: _Registrar | None = None) -> None:
        self._database = database
        self._registrar: _Registrar = registrar or persist_model_import_with_retry
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}
        self._active_id: str | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self, request: DownloadRequest) -> str:
        """Begin an approved download. Refuses while another download is
        running (`download_active`); answers with the id to poll."""
        with self._lock:
            if self._active_id is not None:
                raise ModelRuntimeError("download_active")
            record = _Record(
                request=request,
                files=[
                    DownloadFileProgress(
                        filename=spec.filename,
                        bytes_downloaded=0,
                        bytes_total=spec.size_bytes,
                    )
                    for spec in request.files
                ],
            )
            record.thread = threading.Thread(
                target=self._run,
                args=(request.download_id,),
                daemon=True,
                name=f"controlgauge-download-{request.download_id[:8]}",
            )
            self._records[request.download_id] = record
            self._active_id = request.download_id
        record.thread.start()
        return request.download_id

    def status(self, download_id: str) -> DownloadStatus:
        """Structural poll: an in-memory snapshot, no I/O, no runtime
        contact, no paths -- filenames and byte counts only."""
        with self._lock:
            record = self._records.get(download_id)
            if record is None:
                raise ModelRuntimeError("download_not_found")
            return DownloadStatus(
                download_id=download_id,
                state=record.state,
                files=tuple(record.files),
                error_code=record.error_code,
            )

    def cancel(self, download_id: str) -> DownloadStatus:
        """Request cancellation; the worker notices at the next chunk
            boundary and retains the part. Cancelling an unknown id is
        `download_not_found`; cancelling a terminal download is a no-op
        returning its status."""
        with self._lock:
            record = self._records.get(download_id)
            if record is None:
                raise ModelRuntimeError("download_not_found")
            record.cancel.set()
            return DownloadStatus(
                download_id=download_id,
                state=record.state,
                files=tuple(record.files),
                error_code=record.error_code,
            )

    def active(self) -> str | None:
        """The running download's id, or ``None`` -- the structural proof
        that nothing downloads without an explicit approval POST."""
        with self._lock:
            return self._active_id

    def shutdown(self) -> None:
        """Cancel whatever is running and bound the wait (called beside
        `model_runtime.stop_all()` at process shutdown)."""
        with self._lock:
            records = list(self._records.values())
            for record in records:
                record.cancel.set()
            threads = [record.thread for record in records if record.thread is not None]
        for thread in threads:
            thread.join(timeout=5.0)

    # -- worker ---------------------------------------------------------

    def _snapshot_locked(self, record: _Record, download_id: str) -> DownloadStatus:
        return DownloadStatus(
            download_id=download_id,
            state=record.state,
            files=tuple(record.files),
            error_code=record.error_code,
        )

    def _update(self, download_id: str, **changes: object) -> None:
        with self._lock:
            record = self._records.get(download_id)
            if record is None:
                return
            for key, value in changes.items():
                setattr(record, key, value)

    def _run(self, download_id: str) -> None:
        with self._lock:
            record = self._records[download_id]
            request = record.request
        try:
            self._update(download_id, state=_STATE_DOWNLOADING)
            final_paths: list[Path] = []
            digests: list[str] = []
            for index, spec in enumerate(request.files):

                def progress(
                    bytes_downloaded: int, index: int = index, spec: DownloadFileSpec = spec
                ) -> None:
                    with self._lock:
                        entry = self._records.get(download_id)
                        if entry is None or index >= len(entry.files):
                            return
                        entry.files[index] = DownloadFileProgress(
                            filename=spec.filename,
                            bytes_downloaded=bytes_downloaded,
                            bytes_total=spec.size_bytes,
                        )

                path, digest = download_file(spec, request.dest_dir, record.cancel, progress)
                final_paths.append(path)
                digests.append(digest)
            self._update(download_id, state=_STATE_VERIFYING)
            self._register(request, final_paths, digests)
            self._update(download_id, state=_STATE_COMPLETED, error_code=None)
            logger.info(
                "download_completed",
                extra={
                    "download_id": download_id,
                    "origin": request.origin,
                    "verification": request.verification,
                    "file_count": len(request.files),
                },
            )
        except _DownloadCancelled:
            # The parts stay on disk: the next attempt of the same file
            # resumes from them (documented V0.1 semantics).
            self._update(download_id, state=_STATE_CANCELLED, error_code=None)
            logger.info("download_cancelled", extra={"download_id": download_id})
        except ModelRuntimeError as exc:
            self._update(download_id, state=_STATE_FAILED, error_code=exc.code)
            logger.warning(
                "download_failed",
                extra={"download_id": download_id, "error_code": exc.code},
            )
        except Exception:
            # Worker-thread boundary: a bug must fail the download, never
            # kill the thread silently. Fixed event + fixed code; no
            # exception text (it may carry local paths, T-10).
            logger.exception("download_unexpected_failure", extra={"download_id": download_id})
            self._update(download_id, state=_STATE_FAILED, error_code="download_network_error")
        finally:
            with self._lock:
                if self._active_id == download_id:
                    self._active_id = None
                self._prune_locked()

    def _prune_locked(self) -> None:
        """Keep only the newest ``_MAX_RECORDS`` terminal records (active
        records are never pruned)."""
        terminal = [
            key
            for key, record in self._records.items()
            if record.state in _TERMINAL_STATES and key != self._active_id
        ]
        excess = len(terminal) - _MAX_RECORDS
        for key in terminal[: max(excess, 0)]:
            del self._records[key]

    # -- registration ---------------------------------------------------

    def _register(
        self, request: DownloadRequest, final_paths: list[Path], digests: list[str]
    ) -> None:
        """The verified-import path, fail-closed: the primary (first)
        file must sniff as a supported GGUF container BEFORE any database
        write, then one duplicate-aware transaction creates the row and
        its 1:1 provenance. Multi-shard entries register their primary
        file (shard 1 -- the file llama-server is given);
        `model_source.files_json` covers EVERY downloaded file, closing
        the 7B shard provenance gap (S6-05)."""
        primary = final_paths[0]
        try:
            size = primary.stat().st_size
            with primary.open("rb") as fh:
                head = fh.read(_SNIFF_BYTES)
        except OSError:
            raise ModelRuntimeError("model_file_not_found") from None
        gguf.validate_gguf_head(head, size)
        # Full provenance for the registry: every file, its computed
        # digest and on-disk size. A stat failure here is a fixed error
        # -- never a partial registration (fail closed).
        files: list[SourceFile] = []
        try:
            for path, digest in zip(final_paths, digests, strict=True):
                files.append(
                    SourceFile(filename=path.name, sha256=digest, size_bytes=path.stat().st_size)
                )
        except OSError:
            raise ModelRuntimeError("model_file_not_found") from None
        provenance = ImportProvenance(
            origin=request.origin,
            verification=request.verification,
            catalog_model_id=request.catalog_model_id,
            source_url=request.source_url,
            revision=request.revision,
            license_notice=request.license_notice,
            files=tuple(files),
        )
        record, duplicate = self._registrar(
            self._database,
            role=request.role,
            display_name=request.display_name,
            path=str(primary),
            sha256=digests[0],
            size_bytes=size,
            provenance=provenance,
        )
        logger.info(
            "download_registered",
            extra={
                "model_id": getattr(record, "id", None),
                "role": request.role,
                "origin": request.origin,
                "file_count": len(files),
                "duplicate": duplicate,
            },
        )


def new_download_id() -> str:
    """A fresh opaque download id (uuid4 hex)."""
    return uuid.uuid4().hex
