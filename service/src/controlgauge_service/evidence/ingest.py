"""Background parsing of uploaded artifacts (Sprint 4).

One worker thread takes artifact ids from a queue, marks the artifact
`parsing`, runs the contained parser on its stored file
(`sandbox.ContainedRunner`), **validates the worker's output** (it is
untrusted, D-022), and stores the sections and the final `parse_status`
in one transaction. Every outcome maps to a `DATABASE.md` §3 status with a
fixed `parse_detail` category.

At startup, artifacts left `parsing` by an interrupted run go back to
`pending`, and every `pending` artifact is queued again; leftover scratch
directories and sandbox profiles are removed.

Logs carry the artifact id, status and detail category only -- never a
filename, path or any document text (T-10).
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from ..db import Database
from ..db.base import utc_now
from ..db.models import SECTION_LOCATOR_MAX, Artifact, ArtifactSection
from . import limits
from .sandbox import ContainedRunner, ParseRunner, SandboxResult
from .store import EvidenceStore

logger = logging.getLogger("controlgauge_service.ingest")

TERMINAL_STATUSES = frozenset({"parsed", "partial", "empty", "unsupported_format", "failed"})
_WITH_SECTIONS = frozenset({"parsed", "partial"})
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
_PARSER = re.compile(r"^[A-Za-z0-9._/-]{1,50}$")
_LOCATOR_DISALLOWED = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
#: A section is at most the chunk target, plus a CSV header line.
MAX_SECTION_CHARS = limits.SECTION_TARGET_CHARS * 4

_OUTCOME_DETAIL = {
    "timeout": "timeout",
    "cpu_limit": "cpu_limit",
    "memory_limit": "memory_limit",
    "output_limit": "output_limit",
    "launch_failed": "sandbox_launch_failed",
    "containment_unavailable": "containment_unavailable",
}


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str | None
    parser: str | None
    sections: list[tuple[str, str]]


def _failed(detail: str) -> Outcome:
    return Outcome("failed", detail, None, [])


def interpret(result: SandboxResult) -> Outcome:
    """Turns a sandbox result into a validated outcome. Anything malformed,
    inconsistent or out of bounds becomes `failed/invalid_worker_output`;
    nothing from an invalid payload is kept."""
    if result.outcome != "exited":
        return _failed(_OUTCOME_DETAIL.get(result.outcome, "sandbox_error"))
    if result.exit_code != 0:
        return _failed("worker_crashed")
    invalid = _failed("invalid_worker_output")
    try:
        payload: Any = json.loads(result.output.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return invalid
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return invalid
    status = payload.get("status")
    detail = payload.get("detail")
    parser = payload.get("parser")
    sections = payload.get("sections", [])
    if status not in TERMINAL_STATUSES:
        return invalid
    if detail is not None and not (isinstance(detail, str) and _CODE.fullmatch(detail)):
        return invalid
    if parser is not None and not (isinstance(parser, str) and _PARSER.fullmatch(parser)):
        return invalid
    if not isinstance(sections, list) or len(sections) > limits.MAX_SECTIONS:
        return invalid
    if (status in _WITH_SECTIONS) != bool(sections):
        return invalid
    total = 0
    cleaned: list[tuple[str, str]] = []
    for item in sections:
        if not isinstance(item, dict) or set(item) != {"content", "locator"}:
            return invalid
        content, locator = item["content"], item["locator"]
        if not (isinstance(content, str) and isinstance(locator, str)):
            return invalid
        if not content.strip() or len(content) > MAX_SECTION_CHARS or "\x00" in content:
            return invalid
        if not locator or len(locator) > SECTION_LOCATOR_MAX:
            return invalid
        if _LOCATOR_DISALLOWED.search(locator):
            return invalid
        total += len(content)
        if total > limits.MAX_EXTRACTED_CHARS:
            return invalid
        cleaned.append((content, locator))
    return Outcome(status, detail, parser, cleaned)


class IngestionService:
    """Owns the parse queue. `runner` defaults to the production
    `ContainedRunner`; tests may inject another runner explicitly."""

    def __init__(
        self, database: Database, store: EvidenceStore, runner: ParseRunner | None = None
    ) -> None:
        self.database = database
        self.store = store
        self.runner = (
            runner
            if runner is not None
            else ContainedRunner(store.scratch_root, limits.WORKER_LIMITS)
        )
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self.store.reset_scratch()
        if sys.platform == "win32":
            try:
                from .sandbox_windows import remove_stale_containers

                remove_stale_containers()
            except Exception as exc:  # noqa: BLE001 - cleanup is best effort
                logger.warning("sandbox_cleanup_failed", extra={"exc_type": type(exc).__name__})
        with self.database.session(write=True) as session:
            session.execute(
                update(Artifact)
                .where(Artifact.parse_status == "parsing")
                .values(parse_status="pending")
            )
            pending = list(
                session.scalars(
                    select(Artifact.id)
                    .where(Artifact.parse_status == "pending")
                    .order_by(Artifact.uploaded_at)
                )
            )
        self._thread = threading.Thread(target=self._run, name="evidence-ingest", daemon=True)
        self._thread.start()
        for artifact_id in pending:
            self.enqueue(artifact_id)

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=timeout)
            self._thread = None

    def enqueue(self, artifact_id: str) -> None:
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._queue.put(artifact_id)

    def wait_idle(self, timeout: float) -> bool:
        """Test helper: waits until the queue is drained."""
        return self._idle.wait(timeout)

    # --- work --------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            artifact_id = self._queue.get()
            if artifact_id is None:
                return
            try:
                self.process(artifact_id)
            except Exception as exc:  # noqa: BLE001 - one artifact must not stop the queue
                logger.error(
                    "ingest_unexpected_error",
                    extra={"artifact_id": artifact_id, "exc_type": type(exc).__name__},
                )
                self._finish(artifact_id, _failed("internal_error"))
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending == 0:
                        self._idle.set()

    def process(self, artifact_id: str) -> None:
        with self.database.session(write=True) as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.parse_status != "pending":
                return
            artifact.parse_status = "parsing"
            path: Path = self.store.path_for(artifact.assessment_id, artifact.sha256_hash)
            fmt = artifact.format
        if not path.is_file():
            outcome = _failed("stored_file_missing")
        else:
            try:
                outcome = interpret(self.runner.run(path, fmt))
            except Exception as exc:  # noqa: BLE001 - sandbox setup errors are fixed categories
                logger.error(
                    "sandbox_setup_failed",
                    extra={"artifact_id": artifact_id, "exc_type": type(exc).__name__},
                )
                outcome = _failed("sandbox_setup_failed")
        self._finish(artifact_id, outcome)

    def _finish(self, artifact_id: str, outcome: Outcome) -> None:
        with self.database.session(write=True) as session:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.parse_status in TERMINAL_STATUSES:
                return
            for index, (content, locator) in enumerate(outcome.sections):
                session.add(
                    ArtifactSection(
                        id=str(uuid.uuid4()),
                        artifact_id=artifact_id,
                        sequence_index=index,
                        content=content,
                        locator=locator,
                    )
                )
            artifact.parse_status = outcome.status
            artifact.parse_detail = outcome.detail
            artifact.parser_version = outcome.parser
            artifact.section_count = len(outcome.sections)
            artifact.parsed_at = utc_now()
        logger.info(
            "artifact_parsed",
            extra={
                "artifact_id": artifact_id,
                "parse_status": outcome.status,
                "parse_detail": outcome.detail,
                "section_count": len(outcome.sections),
            },
        )


def start_ingestion(database: Database) -> IngestionService | None:
    """Starts ingestion beside the database for either entry point. A
    failure here is logged with a fixed category and leaves the service
    running without uploads (they answer 503), rather than refusing to
    start the whole service."""
    if database.data_dir is None:
        return None
    try:
        service = IngestionService(database, EvidenceStore(database.data_dir))
        service.start()
    except Exception as exc:  # noqa: BLE001 - fixed category, service keeps running
        logger.error("ingestion_start_failed", extra={"exc_type": type(exc).__name__})
        return None
    return service
