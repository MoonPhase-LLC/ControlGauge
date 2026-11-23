"""S5-05: the model import/runtime routes over TestClient + seeded DB.

The manager is the real `ModelRuntimeManager` with the provider class
replaced by a fake (no children are ever spawned here; the real provider
lifecycle is `test_model_lifecycle.py`'s subject and the real-model path is
`test_model_integration.py`'s).
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import initialize_database
from controlgauge_service.db.models import ModelFile
from controlgauge_service.evidence.filetype import UploadRejected, clean_filename
from controlgauge_service.model_runtime import manager as manager_module
from controlgauge_service.model_runtime.config import (
    MAX_MODEL_FILE_BYTES,
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.model_runtime.protocols import HealthStatus, ModelDescriptor
from controlgauge_service.routes import models as models_routes
from controlgauge_service.routes.models import (
    IMPORT_PROVENANCE_WARNING,
    _clean_display_name,
)

from .conftest import VALID_TOKEN

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}

GGUF_HEAD_V3 = b"GGUF" + struct.pack("<I", 3)
BAD_MAGIC_HEAD = b"NOPE" + struct.pack("<I", 3)


def _write_gguf(path: Path, head: bytes = GGUF_HEAD_V3, body: bytes | None = None) -> Path:
    payload = head + (b"\x00" * (4096 - len(head)) if body is None else body)
    path.write_bytes(payload)
    return path


class _FakeProvider:
    """Stand-in for `LlamaCppProvider`: records lifecycle calls, never
    spawns anything."""

    instances: ClassVar[list[_FakeProvider]] = []
    next_health: ClassVar[HealthStatus | None] = None
    health_check_calls: ClassVar[int] = 0

    def __init__(
        self,
        config: GenerationServerConfig | EmbeddingServerConfig,
        runtime_binary: Path,
        *,
        role: str,
        on_dimension_learned: Any = None,
    ) -> None:
        self.config = config
        self.role = role
        self.on_dimension_learned = on_dimension_learned
        self.started = False
        self.stopped = False
        _FakeProvider.instances.append(self)

    @property
    def model_id(self) -> str:
        return self.config.model_id

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def health_check(self) -> HealthStatus:
        _FakeProvider.health_check_calls += 1
        if _FakeProvider.next_health is not None:
            return _FakeProvider.next_health
        return HealthStatus(state="ready", detail_code=None, checked_at=datetime.now(UTC))

    def describe(self) -> ModelDescriptor:
        return ModelDescriptor(
            model_id=self.config.model_id,
            display_name=self.config.model_id,
            role=self.role,  # type: ignore[arg-type]
            context_tokens=self.config.context_tokens,
        )


@pytest.fixture()
def model_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """App + client + database with the fake-provider manager wired."""
    monkeypatch.setattr(manager_module, "LlamaCppProvider", _FakeProvider)
    _FakeProvider.instances = []
    _FakeProvider.next_health = None
    _FakeProvider.health_check_calls = 0
    database = initialize_database(tmp_path)
    app = create_app(
        Settings(session_token=VALID_TOKEN),
        database=database,
        model_runtime=ModelRuntimeManager(tmp_path / "llama-server"),
    )
    client = TestClient(app)
    yield SimpleNamespace(client=client, database=database, tmp_path=tmp_path)
    client.close()
    database.dispose()


def _import(client: TestClient, path: Path, role: str = "generation", **extra: Any) -> Any:
    body: dict[str, Any] = {"path": str(path), "role": role, **extra}
    return client.post("/models/import", json=body, headers=AUTH)


def _imported_id(client: TestClient, path: Path, role: str = "generation") -> str:
    response = _import(client, path, role)
    assert response.status_code == 201, response.text
    model_id: str = response.json()["model"]["id"]
    return model_id


# --- import: auth + validation order ------------------------------------------


def test_import_requires_auth(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    response = model_env.client.post(
        "/models/import", json={"path": str(path), "role": "generation"}
    )
    assert response.status_code == 401


def test_import_rejects_relative_path(model_env: SimpleNamespace) -> None:
    response = model_env.client.post(
        "/models/import",
        json={"path": "relative/model.gguf", "role": "generation"},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_path_invalid"


def test_import_rejects_wrong_extension(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = _write_gguf(tmp_path / "model.bin")
    response = _import(model_env.client, path)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_import_rejects_missing_file(model_env: SimpleNamespace, tmp_path: Path) -> None:
    response = _import(model_env.client, tmp_path / "absent.gguf")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_file_not_found"


def test_import_rejects_non_gguf_magic(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = _write_gguf(tmp_path / "model.gguf", head=BAD_MAGIC_HEAD)
    response = _import(model_env.client, path)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_gguf_magic"


def test_import_rejects_unsupported_gguf_version(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "model.gguf", head=b"GGUF" + struct.pack("<I", 9))
    response = _import(model_env.client, path)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_gguf_version"


def test_import_rejects_undersized_file(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = tmp_path / "tiny.gguf"
    path.write_bytes(GGUF_HEAD_V3)
    response = _import(model_env.client, path)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "model_file_too_small"


def test_import_rejects_oversized(
    model_env: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route refuses any file whose stat size exceeds the sanity bound,
    at stat time -- long before any hashing. The oversized size is injected
    through the ``_read_head_and_size`` seam instead of materializing a
    256 GiB file: ``truncate()`` is a free sparse hole on Linux, but the
    Windows file-extension path eagerly consumes disk, which exhausted CI
    runners mid-suite (ENOSPC, 2026-11-18 and 2026-11-23). A POSIX-only
    companion below proves the same refusal against a real sparse file."""
    path = _write_gguf(tmp_path / "huge.gguf")
    real_head, real_size = models_routes._read_head_and_size(path)
    assert real_size < MAX_MODEL_FILE_BYTES
    monkeypatch.setattr(
        models_routes,
        "_read_head_and_size",
        lambda _path: (real_head, MAX_MODEL_FILE_BYTES + 1),
    )
    response = _import(model_env.client, path)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "model_file_too_large"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="NTFS extends non-sparse files eagerly; a 256 GiB truncate exhausts the disk "
    "(POSIX sparse holes are free)",
)
def test_import_rejects_oversized_real_sparse_file_posix(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """End-to-end form of the oversized refusal with a REAL sparse file
    (free on POSIX filesystems): refused from its stat size, before any
    hashing. Windows coverage for the same decision lives in the seam test
    above."""
    path = _write_gguf(tmp_path / "huge.gguf")
    with path.open("rb+") as fh:
        fh.truncate(MAX_MODEL_FILE_BYTES + 1)
    response = _import(model_env.client, path)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "model_file_too_large"


def test_import_rejects_unknown_role_and_extra_fields(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    bad_role = model_env.client.post(
        "/models/import", json={"path": str(path), "role": "sampler"}, headers=AUTH
    )
    assert bad_role.status_code == 422
    assert bad_role.json()["error"]["code"] == "validation_failed"
    extra = model_env.client.post(
        "/models/import",
        json={"path": str(path), "role": "generation", "checksum": "deadbeef"},
        headers=AUTH,
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["fields"][0]["code"] == "unknown_field"


def test_import_accepts_space_in_filename(model_env: SimpleNamespace, tmp_path: Path) -> None:
    """F2: the display-name class is `evidence/filetype.py`'s, exactly --
    plain spaces are legal, so a space-named GGUF imports."""
    path = _write_gguf(tmp_path / "Meta-Llama 3 8B.Q4_K_M.gguf", body=b"w" * 5000)
    response = _import(model_env.client, path)
    assert response.status_code == 201, response.text
    assert response.json()["model"]["display_name"] == "Meta-Llama 3 8B.Q4_K_M.gguf"


def test_import_rejects_line_separator_in_filename(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """F2, disallowed side: the shared class still refuses the Unicode
    line separator (and paragraph separator -- same class)."""
    path = _write_gguf(tmp_path / "bad\u2028name.gguf", body=b"w" * 5000)
    response = _import(model_env.client, path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_path_invalid"


@pytest.mark.parametrize(
    ("probe", "description"),
    [
        (" ", "plain space"),
        ("\u00a0", "no-break space"),
        ("\u2028", "line separator"),
        ("\u2029", "paragraph separator"),
        ("<", "Windows-forbidden"),
        (">", "Windows-forbidden"),
        (":", "Windows-forbidden"),
        ("?", "Windows-forbidden"),
        ("\t", "control character"),
        ("\x7f", "control character"),
        ("\u00e9", "ordinary letter"),
    ],
)
def test_display_name_rule_matches_evidence_clean_filename(probe: str, description: str) -> None:
    """F2 drift guard: the model import surface accepts/rejects exactly
    what `evidence/filetype.clean_filename` accepts/rejects for every
    probed character. The shared class makes drift structurally
    impossible; this pins the behavior anyway."""
    name = f"m{probe}odel.gguf"
    try:
        clean_filename(name)
        evidence_accepts = True
    except UploadRejected:
        evidence_accepts = False
    try:
        _clean_display_name(name)
        models_accept = True
    except ModelRuntimeError:
        models_accept = False
    assert models_accept is evidence_accepts, f"probe {probe!r} ({description})"


def test_import_rejects_directory_path(model_env: SimpleNamespace, tmp_path: Path) -> None:
    """N1: only regular files are candidates; a directory is refused from
    its stat entry with the fixed not-regular code."""
    directory = tmp_path / "not-a-model.gguf"
    directory.mkdir()
    response = _import(model_env.client, directory)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_file_not_regular"


@pytest.mark.skipif(sys.platform == "win32", reason="os.mkfifo is POSIX-only")
def test_import_rejects_fifo_path(model_env: SimpleNamespace, tmp_path: Path) -> None:
    """N1: a FIFO at the imported path must be refused from its stat
    entry, BEFORE any open -- open() on a writerless FIFO would block
    forever (self-DoS). A regression here hangs this test."""
    mkfifo = getattr(os, "mkfifo", None)
    assert mkfifo is not None  # POSIX-guarded above
    fifo = tmp_path / "pipe.gguf"
    mkfifo(fifo)
    response = _import(model_env.client, fifo)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "model_file_not_regular"


# --- import: persistence + duplicate + provenance -------------------------------


def test_import_persists_record(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = _write_gguf(tmp_path / "qwen-model.gguf", body=b"weights" * 600)
    response = _import(model_env.client, path)
    assert response.status_code == 201
    model = response.json()["model"]
    assert model["role"] == "generation"
    assert model["display_name"] == "qwen-model.gguf"
    assert model["size_bytes"] == path.stat().st_size
    assert model["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    fetched = model_env.client.get(f"/models/{model['id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json()["display_name"] == "qwen-model.gguf"
    listed = model_env.client.get("/models", headers=AUTH)
    assert [m["id"] for m in listed.json()["models"]] == [model["id"]]


def test_import_response_carries_provenance_warning(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    response = _import(model_env.client, path)
    body = response.json()
    assert body["provenance"] == "unverified_import"
    assert body["warning"] == IMPORT_PROVENANCE_WARNING
    assert body["duplicate"] is False


def test_import_duplicate_returns_existing_with_flag(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    original = _write_gguf(tmp_path / "original.gguf", body=b"same-bytes" * 500)
    first = _import(model_env.client, original)
    assert first.status_code == 201
    first_id = first.json()["model"]["id"]
    assert first.json()["model"]["last_verified_at"] is None

    moved = _write_gguf(tmp_path / "moved.gguf", body=b"same-bytes" * 500)
    second = _import(model_env.client, moved)
    assert second.status_code == 200
    body = second.json()
    assert body["duplicate"] is True
    assert body["model"]["id"] == first_id
    assert body["model"]["last_verified_at"] is not None

    # The record now points at the newest location (checked in the DB:
    # the path is never exposed by the API).
    with model_env.database.session() as session:
        record = session.get(ModelFile, first_id)
        assert record is not None
        assert record.path == str(moved)


def test_import_same_content_different_role_is_not_a_duplicate(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "dual-role.gguf", body=b"same-bytes" * 500)
    generation = _import(model_env.client, path, role="generation")
    embedding = _import(model_env.client, path, role="embedding")
    assert generation.status_code == 201 and embedding.status_code == 201
    assert generation.json()["model"]["id"] != embedding.json()["model"]["id"]
    assert embedding.json()["duplicate"] is False


def test_import_logs_never_contain_path_or_filename(
    model_env: SimpleNamespace, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    marker_dir = tmp_path / "secret-user-marker"
    marker_dir.mkdir()
    path = _write_gguf(marker_dir / "very-distinct-name-9f2.gguf", body=b"x" * 4096)
    with caplog.at_level(logging.INFO):
        response = _import(model_env.client, path)
    assert response.status_code == 201
    log_text = "\n".join(
        record.getMessage() + " " + str(record.__dict__) for record in caplog.records
    )
    assert "very-distinct-name-9f2" not in log_text
    assert str(marker_dir) not in log_text
    assert str(path) not in log_text
    # The response body carries the display name by design, but never the
    # directory part of the path.
    assert "secret-user-marker" not in response.text


# --- get / list: closed schemas, no runtime surface ------------------------------


def test_models_list_closed_schema(model_env: SimpleNamespace, tmp_path: Path) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    _import(model_env.client, path)
    listed = model_env.client.get("/models", headers=AUTH)
    assert listed.status_code == 200
    body = listed.json()
    assert set(body) == {"models", "runtime"}
    assert set(body["runtime"]) == {"generation", "embedding"}
    for status in body["runtime"].values():
        assert set(status) == {"state", "detail_code", "checked_at", "model_id"}
    for model in body["models"]:
        assert set(model) == {
            "id",
            "role",
            "display_name",
            "sha256",
            "size_bytes",
            "imported_at",
            "last_verified_at",
            "embedding_dimensions",
            "embedding_normalization",
            "embedding_similarity_metric",
        }


def test_get_model_unknown_shape_is_validation_error(model_env: SimpleNamespace) -> None:
    response = model_env.client.get("/models/not-a-uuid", headers=AUTH)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["fields"][0]["field"] == "model_id"


def test_models_list_is_structural_and_never_checks_the_runtime(
    model_env: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: `GET /models` answers from the manager's last-known state and
    never performs a live capability check -- a hung child must not stall
    or D-026-fail-close the list. Live checks ride
    `POST /models/{id}/health` (its own test below)."""
    path = _write_gguf(tmp_path / "model.gguf")
    model_id = _imported_id(model_env.client, path)
    started = model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
    assert started.status_code == 200
    checks_after_start = _FakeProvider.health_check_calls

    def _refuse_live_check(role: Any) -> HealthStatus:
        raise AssertionError("GET /models must not perform a live health check")

    monkeypatch.setattr(ModelRuntimeManager, "status", _refuse_live_check)
    listed = model_env.client.get("/models", headers=AUTH)
    assert listed.status_code == 200
    generation = listed.json()["runtime"]["generation"]
    # Last-known state: the start route's real check recorded it.
    assert generation["state"] == "ready"
    assert generation["model_id"] == model_id
    assert generation["checked_at"] is not None
    assert listed.json()["runtime"]["embedding"]["state"] == "stopped"
    # Not one live check ran anywhere on the list path.
    assert _FakeProvider.health_check_calls == checks_after_start


def test_health_route_still_performs_a_real_check(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """F1 counterpart: `POST /models/{id}/health` remains the bounded REAL
    capability check (D-023); `unhealthy` is a status, not an error."""
    path = _write_gguf(tmp_path / "model.gguf")
    model_id = _imported_id(model_env.client, path)
    started = model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
    assert started.status_code == 200
    assert _FakeProvider.health_check_calls >= 1  # the start route's post-load check

    _FakeProvider.next_health = HealthStatus(
        state="unhealthy", detail_code="request_timeout", checked_at=datetime.now(UTC)
    )
    response = model_env.client.post(f"/models/{model_id}/health", json={}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["state"] == "unhealthy"
    assert response.json()["detail_code"] == "request_timeout"
    assert response.json()["model_id"] == model_id
    assert _FakeProvider.health_check_calls >= 2


def test_no_route_exposes_runtime_port_pid_or_credential(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """Walk every model-route response across a full lifecycle: no port,
    pid, credential, path, or auth material ever appears (T-10/T-23)."""
    path = _write_gguf(tmp_path / "model.gguf")
    responses = []
    responses.append(_import(model_env.client, path))
    model_id = responses[0].json()["model"]["id"]
    responses.append(model_env.client.get("/models", headers=AUTH))
    responses.append(model_env.client.get(f"/models/{model_id}", headers=AUTH))
    responses.append(model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH))
    responses.append(model_env.client.post(f"/models/{model_id}/health", json={}, headers=AUTH))
    responses.append(model_env.client.post(f"/models/{model_id}/stop", json={}, headers=AUTH))

    forbidden_keys = {"port", "pid", "credential", "path", "authorization", "api_key"}

    def walk(node: Any, forbidden: set[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert str(key).lower() not in forbidden, key
                walk(value, forbidden)
        elif isinstance(node, list):
            for item in node:
                walk(item, forbidden)

    for response in responses:
        assert response.status_code in (200, 201), response.text
        walk(response.json(), forbidden_keys)
        assert "Bearer " not in response.text
        assert str(path) not in response.text


# --- start / stop / health -------------------------------------------------------


def test_start_unknown_id_404(model_env: SimpleNamespace) -> None:
    response = model_env.client.post(
        "/models/0f8fad5b-d9cb-469f-a165-70867728950e/start", json={}, headers=AUTH
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_start_without_runtime_binary_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`runtime_binary_missing` fails closed as 503 (spec'd mapping)."""
    monkeypatch.setattr(manager_module, "LlamaCppProvider", _FakeProvider)
    _FakeProvider.instances = []
    database = initialize_database(tmp_path)
    try:
        app = create_app(
            Settings(session_token=VALID_TOKEN),
            database=database,
            model_runtime=ModelRuntimeManager(None),
        )
        client = TestClient(app)
        path = _write_gguf(tmp_path / "model.gguf")
        imported = client.post(
            "/models/import", json={"path": str(path), "role": "generation"}, headers=AUTH
        )
        model_id = imported.json()["model"]["id"]
        response = client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "runtime_binary_missing"
        client.close()
    finally:
        database.dispose()


def test_start_stop_and_health_reflect_runtime_state(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    model_id = _imported_id(model_env.client, path)

    # Not started: health reports stopped, nothing is contacted.
    stopped = model_env.client.post(f"/models/{model_id}/health", json={}, headers=AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["checked_at"] is None

    started = model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
    assert started.status_code == 200
    assert started.json()["state"] == "ready"
    assert started.json()["model_id"] == model_id
    assert started.json()["checked_at"] is not None
    assert len(_FakeProvider.instances) == 1
    assert _FakeProvider.instances[0].started

    health = model_env.client.post(f"/models/{model_id}/health", json={}, headers=AUTH)
    assert health.json()["state"] == "ready"
    assert health.json()["model_id"] == model_id

    # Runtime status rides GET /models too.
    listed = model_env.client.get("/models", headers=AUTH)
    assert listed.json()["runtime"]["generation"]["state"] == "ready"

    stopped_after = model_env.client.post(f"/models/{model_id}/stop", json={}, headers=AUTH)
    assert stopped_after.status_code == 200
    assert stopped_after.json()["state"] == "stopped"
    assert _FakeProvider.instances[0].stopped
    # Stop is idempotent.
    again = model_env.client.post(f"/models/{model_id}/stop", json={}, headers=AUTH)
    assert again.status_code == 200
    assert again.json()["state"] == "stopped"


def test_start_request_is_closed_and_bounds_options(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "model.gguf")
    model_id = _imported_id(model_env.client, path)

    unknown = model_env.client.post(
        f"/models/{model_id}/start",
        json={"start_options": {"temperature": 0.5}},
        headers=AUTH,
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["fields"][0]["code"] == "unknown_field"

    out_of_bounds = model_env.client.post(
        f"/models/{model_id}/start",
        json={"start_options": {"context_tokens": 1}},
        headers=AUTH,
    )
    assert out_of_bounds.status_code == 422

    ok = model_env.client.post(
        f"/models/{model_id}/start",
        json={"start_options": {"context_tokens": 4096, "threads": 4}},
        headers=AUTH,
    )
    assert ok.status_code == 200
    config = _FakeProvider.instances[0].config
    assert isinstance(config, GenerationServerConfig)
    assert config.context_tokens == 4096
    assert config.threads == 4


def test_embedding_start_rejects_meaningless_max_output_tokens(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    path = _write_gguf(tmp_path / "embed.gguf")
    model_id = _imported_id(model_env.client, path, role="embedding")
    response = model_env.client.post(
        f"/models/{model_id}/start",
        json={"start_options": {"max_output_tokens": 32}},
        headers=AUTH,
    )
    assert response.status_code == 422
    fields = response.json()["error"]["fields"]
    assert fields[0]["field"] == "start_options.max_output_tokens"


def test_dimension_pinned_to_record_via_callback(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """The manager's learn-and-pin callback persists the observed embedding
    dimensionality on the model_file row (S5-04 -> S5-05), visible via
    `GET /models/{id}`."""
    path = _write_gguf(tmp_path / "embed.gguf")
    model_id = _imported_id(model_env.client, path, role="embedding")
    started = model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
    assert started.status_code == 200

    provider = _FakeProvider.instances[0]
    assert provider.on_dimension_learned is not None
    provider.on_dimension_learned(768)

    fetched = model_env.client.get(f"/models/{model_id}", headers=AUTH)
    assert fetched.json()["embedding_dimensions"] == 768

    # The pinned dimension rides into the next start's expected_dimensions.
    model_env.client.post(f"/models/{model_id}/stop", json={}, headers=AUTH)
    model_env.client.post(f"/models/{model_id}/start", json={}, headers=AUTH)
    embedding_config = _FakeProvider.instances[-1].config
    assert isinstance(embedding_config, EmbeddingServerConfig)
    assert embedding_config.expected_dimensions == 768


def test_health_for_model_not_occupying_its_role_reports_stopped(
    model_env: SimpleNamespace, tmp_path: Path
) -> None:
    """Model B's health does not borrow model A's runtime: a model that is
    not its role's current occupant reports `stopped` without contact."""
    first = _write_gguf(tmp_path / "first.gguf", body=b"a" * 5000)
    second = _write_gguf(tmp_path / "second.gguf", body=b"b" * 5000)
    first_id = _imported_id(model_env.client, first)
    second_id = _imported_id(model_env.client, second)

    started = model_env.client.post(f"/models/{first_id}/start", json={}, headers=AUTH)
    assert started.status_code == 200
    response = model_env.client.post(f"/models/{second_id}/health", json={}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["state"] == "stopped"
    assert response.json()["model_id"] is None
