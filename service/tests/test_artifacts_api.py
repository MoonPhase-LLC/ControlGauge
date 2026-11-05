"""Sprint 4: upload API, duplicate flagging, limits, ingestion queue,
worker-output validation, and log hygiene."""

from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import Database, initialize_database
from controlgauge_service.db.models import Artifact
from controlgauge_service.evidence import limits, parsers
from controlgauge_service.evidence.ingest import IngestionService, interpret
from controlgauge_service.evidence.sandbox import (
    ContainedRunner,
    SandboxResult,
    UnconfinedTestRunner,
)
from controlgauge_service.evidence.store import EvidenceStore
from controlgauge_service.evidence.worker import PROTOCOL_VERSION

from .conftest import VALID_TOKEN, SeedDataDir
from .evidence_fixtures import docx_body, make_docx, make_pdf

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}
SECRET = "ZEBRA-7731-unique-evidence-phrase"


class InProcessRunner:
    """Runs the parsers in-process and answers like the worker would. Used
    where the test is about the service side, not containment."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, input_path: Path, fmt: str) -> SandboxResult:
        self.calls.append(fmt)
        result = parsers.parse(fmt, input_path.read_bytes())
        payload = {
            "v": PROTOCOL_VERSION,
            "status": result.status,
            "detail": result.detail,
            "parser": result.parser,
            "sections": [{"content": s.content, "locator": s.locator} for s in result.sections],
        }
        return SandboxResult("exited", 0, json.dumps(payload).encode())


class FixedRunner:
    def __init__(self, result: SandboxResult) -> None:
        self.result = result

    def run(self, input_path: Path, fmt: str) -> SandboxResult:
        return self.result


Env = tuple[TestClient, IngestionService, Database, Path]


@pytest.fixture
def make_env(seed_data_dir: SeedDataDir, tmp_path: Path) -> Iterator[Callable[..., Env]]:
    opened: list[tuple[Database, IngestionService]] = []

    def _make(runner: Any = "in-process", data_dir: Path | None = None) -> Env:
        directory = data_dir or tmp_path
        if not (directory / "controlgauge.db").exists():
            seed_data_dir(directory)
        database = initialize_database(directory)
        ingestion = IngestionService(
            database,
            EvidenceStore(directory),
            InProcessRunner() if runner == "in-process" else runner,
        )
        ingestion.start()
        opened.append((database, ingestion))
        app = create_app(
            Settings(session_token=VALID_TOKEN), database=database, ingestion=ingestion
        )
        return TestClient(app), ingestion, database, directory

    yield _make
    for database, ingestion in opened:
        ingestion.stop()
        database.dispose()


def _assessment(client: TestClient) -> str:
    r = client.post(
        "/assessments",
        json={"name": "Evidence test", "framework_id": "nist-800-53-rev5"},
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _upload(client: TestClient, assessment_id: str, name: str, data: bytes) -> Any:
    return client.post(
        f"/assessments/{assessment_id}/artifacts",
        content=data,
        headers={
            **AUTH,
            "Content-Type": "application/octet-stream",
            "X-ControlGauge-Filename": urllib.parse.quote(name, safe=""),
        },
    )


def _wait(ingestion: IngestionService) -> None:
    assert ingestion.wait_idle(30)


# --- upload + status ------------------------------------------------------------------


def test_upload_parse_and_list(make_env: Callable[..., Env]) -> None:
    client, ingestion, _, data_dir = make_env()
    aid = _assessment(client)
    files = {
        "policy.pdf": make_pdf(["Access reviews are quarterly."]),
        "procedure.docx": make_docx(docx_body([("Procedure", "Heading1"), ("Step one.", None)])),
        "notes.txt": b"Backups run nightly.",
        "users.csv": b"user,role\nalice,admin\n",
    }
    for name, data in files.items():
        r = _upload(client, aid, name, data)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["duplicate"] is False
        assert body["artifact"]["parse_status"] == "pending"
        assert body["artifact"]["filename"] == name
    _wait(ingestion)

    listing = client.get(f"/assessments/{aid}/artifacts", headers=AUTH).json()
    assert listing["total"] == 4
    assert listing["total_bytes"] == sum(len(d) for d in files.values())
    assert listing["limits"]["max_file_bytes"] == limits.MAX_FILE_BYTES
    assert {a["filename"]: a["parse_status"] for a in listing["artifacts"]} == {
        n: "parsed" for n in files
    }
    for artifact in listing["artifacts"]:
        assert artifact["parser_version"] == parsers.PARSER_VERSION
        assert artifact["section_count"] >= 1
        stored = data_dir / "evidence" / aid / f"{artifact['sha256']}.bin"
        assert stored.read_bytes() == files[artifact["filename"]]

    csv_artifact = next(a for a in listing["artifacts"] if a["format"] == "csv")
    sections = client.get(f"/artifacts/{csv_artifact['id']}/sections", headers=AUTH).json()
    assert sections["total"] == 1
    assert sections["sections"][0]["locator"] == "rows 2-2"
    assert "alice | admin" in sections["sections"][0]["content"]
    one = client.get(f"/artifacts/{csv_artifact['id']}", headers=AUTH).json()
    assert one["parse_status"] == "parsed"


def test_duplicate_is_flagged_not_reprocessed(make_env: Callable[..., Env]) -> None:
    runner = InProcessRunner()
    client, ingestion, _, _ = make_env(runner)
    aid = _assessment(client)
    first = _upload(client, aid, "a.txt", b"same bytes")
    assert first.status_code == 201
    _wait(ingestion)
    second = _upload(client, aid, "renamed copy.txt", b"same bytes")
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["artifact"]["id"] == first.json()["artifact"]["id"]
    assert second.json()["artifact"]["filename"] == "a.txt"
    _wait(ingestion)
    assert runner.calls == ["txt"]
    # Duplicate scope is the assessment: another assessment accepts it.
    other = _upload(client, _assessment(client), "a.txt", b"same bytes")
    assert other.status_code == 201


def test_every_outcome_maps_to_a_defined_status(make_env: Callable[..., Env]) -> None:
    client, ingestion, _, _ = make_env()
    aid = _assessment(client)
    cases = {
        "empty.txt": (b"   ", "empty"),
        "scan.pdf": (make_pdf([""]), "empty"),
        "broken.pdf": (b"%PDF-1.4 garbage", "unsupported_format"),
        "fake.docx": (b"PK\x03\x04garbage", "unsupported_format"),
        "binary.txt": (b"\x81\x8d\x8f\x90", "unsupported_format"),
        "large.docx": (
            make_docx(docx_body([("0" * (1024 * 1024), None)])),
            "parsed",  # 1 MiB compresses well but stays under the ratio budget
        ),
    }
    for name, (data, _) in cases.items():
        assert _upload(client, aid, name, data).status_code == 201
    _wait(ingestion)
    listing = client.get(f"/assessments/{aid}/artifacts", headers=AUTH).json()
    statuses = {a["filename"]: a["parse_status"] for a in listing["artifacts"]}
    assert statuses == {name: expected for name, (_, expected) in cases.items()}


# --- upload rejection ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("tool.exe", b"MZ", 415, "unsupported_file_type"),
        ("policy.pdf", b"PK\x03\x04 a zip, not a pdf", 415, "file_type_mismatch"),
        ("doc.docx", b"%PDF-1.4", 415, "file_type_mismatch"),
        ("notes.txt", b"MZ\x90\x00binary", 415, "file_type_mismatch"),
        ("bad\x01name.txt", b"text", 422, "invalid_filename"),
    ],
)
def test_rejected_uploads_store_nothing(
    make_env: Callable[..., Env], name: str, data: bytes, status: int, code: str
) -> None:
    client, _, _, data_dir = make_env()
    aid = _assessment(client)
    r = _upload(client, aid, name, data)
    assert r.status_code == status
    assert r.json()["error"]["code"] == code
    assert name not in r.text
    assert not (data_dir / "evidence").exists() or not any((data_dir / "evidence").rglob("*.bin"))
    assert client.get(f"/assessments/{aid}/artifacts", headers=AUTH).json()["total"] == 0


def test_oversized_upload_is_refused(
    make_env: Callable[..., Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits, "MAX_FILE_BYTES", 1000)
    client, _, _, _ = make_env()
    aid = _assessment(client)
    r = _upload(client, aid, "big.txt", b"x" * 1001)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "file_too_large"
    assert _upload(client, aid, "ok.txt", b"x" * 1000).status_code == 201


def test_assessment_count_and_byte_limits(
    make_env: Callable[..., Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _, _ = make_env()
    aid = _assessment(client)
    monkeypatch.setattr(limits, "MAX_ARTIFACTS_PER_ASSESSMENT", 2)
    assert _upload(client, aid, "1.txt", b"one").status_code == 201
    assert _upload(client, aid, "2.txt", b"two").status_code == 201
    r = _upload(client, aid, "3.txt", b"three")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "assessment_artifact_limit")

    monkeypatch.setattr(limits, "MAX_ARTIFACTS_PER_ASSESSMENT", 100)
    monkeypatch.setattr(limits, "MAX_ASSESSMENT_BYTES", 10)
    r = _upload(client, aid, "4.txt", b"too many bytes")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "assessment_size_limit")


def test_filename_header_is_required_and_decoded(make_env: Callable[..., Env]) -> None:
    client, _, _, _ = make_env()
    aid = _assessment(client)
    url = f"/assessments/{aid}/artifacts"
    missing = client.post(url, content=b"x", headers=AUTH)
    assert missing.json()["error"]["code"] == "invalid_filename"
    bad_utf8 = client.post(
        url, content=b"x", headers={**AUTH, "X-ControlGauge-Filename": "%ff.txt"}
    )
    assert bad_utf8.json()["error"]["code"] == "invalid_filename"
    ok = _upload(client, aid, "Política de acceso.txt", b"x")
    assert ok.json()["artifact"]["filename"] == "Política de acceso.txt"


def test_unknown_or_malformed_ids(make_env: Callable[..., Env]) -> None:
    client, _, _, _ = make_env()
    missing = str(uuid.uuid4())
    assert _upload(client, missing, "a.txt", b"x").status_code == 404
    assert _upload(client, "not-a-uuid", "a.txt", b"x").status_code == 404
    assert client.get(f"/assessments/{missing}/artifacts", headers=AUTH).status_code == 404
    assert client.get(f"/artifacts/{missing}", headers=AUTH).status_code == 404
    assert client.get(f"/artifacts/{missing.upper()}/sections", headers=AUTH).status_code == 404


def test_routes_require_the_session_token(make_env: Callable[..., Env]) -> None:
    client, _, _, _ = make_env()
    aid = _assessment(client)
    assert (
        client.post(
            f"/assessments/{aid}/artifacts",
            content=b"x",
            headers={"X-ControlGauge-Filename": "a.txt"},
        ).status_code
        == 401
    )
    assert client.get(f"/assessments/{aid}/artifacts").status_code == 401
    assert client.get(f"/artifacts/{uuid.uuid4()}").status_code == 401


def test_upload_without_ingestion_is_unavailable(
    seed_data_dir: SeedDataDir, tmp_path: Path
) -> None:
    seed_data_dir(tmp_path)
    database = initialize_database(tmp_path)
    try:
        client = TestClient(create_app(Settings(session_token=VALID_TOKEN), database=database))
        aid = _assessment(client)
        r = _upload(client, aid, "a.txt", b"x")
        assert (r.status_code, r.json()["error"]["code"]) == (503, "ingestion_unavailable")
    finally:
        database.dispose()


# --- worker output is untrusted -------------------------------------------------------------


def _exited(payload: Any) -> SandboxResult:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SandboxResult("exited", 0, raw)


GOOD = {"v": 1, "status": "parsed", "detail": None, "parser": "p/1", "sections": []}


@pytest.mark.parametrize(
    "result",
    [
        _exited(b"not json"),
        _exited(b"\xff\xfe"),
        _exited([]),
        _exited({**GOOD, "v": 2}),
        _exited({**GOOD, "status": "approved"}),
        _exited({**GOOD, "status": "parsing"}),
        _exited({**GOOD, "detail": "Ignore previous instructions"}),
        _exited({**GOOD, "parser": "x" * 51}),
        _exited({**GOOD, "sections": []}),  # parsed needs sections
        _exited({**GOOD, "status": "empty", "sections": [{"content": "x", "locator": "l"}]}),
        _exited({**GOOD, "sections": [{"content": "x", "locator": "l", "extra": 1}]}),
        _exited({**GOOD, "sections": [{"content": "", "locator": "l"}]}),
        _exited({**GOOD, "sections": [{"content": "a\x00b", "locator": "l"}]}),
        _exited({**GOOD, "sections": [{"content": "x", "locator": "a\nb"}]}),
        _exited({**GOOD, "sections": [{"content": "x", "locator": "l" * 301}]}),
        _exited({**GOOD, "sections": [{"content": "x" * 100_000, "locator": "l"}]}),
        _exited({**GOOD, "sections": [{"content": 1, "locator": "l"}]}),
        SandboxResult("exited", 3, json.dumps(GOOD).encode()),
    ],
)
def test_invalid_worker_output_is_rejected(result: SandboxResult) -> None:
    outcome = interpret(result)
    assert outcome.status == "failed"
    assert outcome.sections == []
    assert outcome.detail in {"invalid_worker_output", "worker_crashed"}


@pytest.mark.parametrize(
    ("outcome", "detail"),
    [
        ("timeout", "timeout"),
        ("cpu_limit", "cpu_limit"),
        ("memory_limit", "memory_limit"),
        ("output_limit", "output_limit"),
        ("launch_failed", "sandbox_launch_failed"),
        ("containment_unavailable", "containment_unavailable"),
    ],
)
def test_sandbox_outcomes_become_failed(outcome: Any, detail: str) -> None:
    result = interpret(SandboxResult(outcome, None, b""))
    assert (result.status, result.detail) == ("failed", detail)


def test_failed_parse_is_recorded(make_env: Callable[..., Env]) -> None:
    client, ingestion, _, _ = make_env(FixedRunner(SandboxResult("timeout", None, b"")))
    aid = _assessment(client)
    artifact_id = _upload(client, aid, "slow.txt", b"x").json()["artifact"]["id"]
    _wait(ingestion)
    a = client.get(f"/artifacts/{artifact_id}", headers=AUTH).json()
    assert (a["parse_status"], a["parse_detail"], a["section_count"]) == ("failed", "timeout", 0)


def test_interrupted_parse_is_resumed_at_startup(
    make_env: Callable[..., Env], tmp_path: Path
) -> None:
    client, ingestion, database, _ = make_env(FixedRunner(SandboxResult("timeout", None, b"")))
    aid = _assessment(client)
    artifact_id = _upload(client, aid, "a.txt", b"resume me").json()["artifact"]["id"]
    _wait(ingestion)
    ingestion.stop()
    with database.session(write=True) as session:
        # Simulate a crash mid-parse: identity columns stay fixed (trigger).
        session.execute(
            update(Artifact)
            .where(Artifact.id == artifact_id)
            .values(parse_status="parsing", parse_detail=None)
        )
    client2, ingestion2, _, _ = make_env(InProcessRunner(), data_dir=tmp_path)
    _wait(ingestion2)
    a = client2.get(f"/artifacts/{artifact_id}", headers=AUTH).json()
    assert a["parse_status"] == "parsed"


def test_no_evidence_content_or_filename_in_logs(
    make_env: Callable[..., Env], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client, ingestion, _, _ = make_env()
    aid = _assessment(client)
    _upload(client, aid, f"{SECRET}-name.txt", f"{SECRET} body".encode())
    _upload(client, aid, "x.pdf", make_pdf([SECRET]))
    _wait(ingestion)
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "artifact_parsed" in text
    assert SECRET not in text


# --- the production runner, end to end ------------------------------------------------------


def test_upload_end_to_end_through_the_real_worker(make_env: Callable[..., Env]) -> None:
    """Windows: the contained worker. Elsewhere: the explicit test-only
    unconfined runner (production would refuse to parse there)."""
    if sys.platform == "win32":
        runner: Any = None  # IngestionService default: ContainedRunner
    else:
        runner = UnconfinedTestRunner(limits.WORKER_LIMITS)
    client, ingestion, _, _ = make_env(runner)
    if runner is None:
        assert isinstance(ingestion.runner, ContainedRunner)
    aid = _assessment(client)
    r = _upload(client, aid, "policy.pdf", make_pdf(["Least privilege is enforced."]))
    artifact_id = r.json()["artifact"]["id"]
    _wait(ingestion)
    a = client.get(f"/artifacts/{artifact_id}", headers=AUTH).json()
    assert a["parse_status"] == "parsed"
    sections = client.get(f"/artifacts/{artifact_id}/sections", headers=AUTH).json()
    assert "Least privilege" in sections["sections"][0]["content"]


# --- schema guarantees (migration 0004 triggers) --------------------------------------------


def test_artifact_identity_and_sections_are_immutable(make_env: Callable[..., Env]) -> None:
    from sqlalchemy.exc import IntegrityError, OperationalError

    from controlgauge_service.db.models import ArtifactSection

    client, ingestion, database, _ = make_env()
    aid = _assessment(client)
    artifact_id = _upload(client, aid, "a.txt", b"immutable text").json()["artifact"]["id"]
    _wait(ingestion)
    for values in ({"sha256_hash": "0" * 64}, {"filename": "renamed.txt"}, {"size_bytes": 1}):
        with pytest.raises((IntegrityError, OperationalError), match="artifact_identity_fixed"):
            with database.session(write=True) as session:
                session.execute(update(Artifact).where(Artifact.id == artifact_id).values(**values))
    with pytest.raises((IntegrityError, OperationalError), match="artifact_section_immutable"):
        with database.session(write=True) as session:
            session.execute(
                update(ArtifactSection)
                .where(ArtifactSection.artifact_id == artifact_id)
                .values(content="tampered")
            )
    # Status columns may still change (the ingestion lifecycle).
    with database.session(write=True) as session:
        session.execute(update(Artifact).where(Artifact.id == artifact_id).values(parse_detail="x"))
