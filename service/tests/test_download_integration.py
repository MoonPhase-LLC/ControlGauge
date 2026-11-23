"""S6-04 (b): the real-network download integration -- the SPRINTS.md
"real model downloaded + verified end to end" criterion in miniature.

Skips cleanly everywhere (Windows CI never sets the gate); runs on demand
with ``CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1`` on a dev box. The module runs
WITHOUT the offline guard but WITH a connect auditor: only loopback and
hosts under ``huggingface.co``/``hf.co`` (the HF API + its CDN, whose
``cas-bridge.xethub.hf.co``-style redirect targets the S6-04 spec's risk
note names) may be contacted, and only IPs those hosts actually resolved
to -- anything else fails the test.

The flow: POST the catalog approval for the nomic embedding entry, poll
the real 84 MB download to ``completed``, assert the registered row's
SHA-256 equals the manifest pin, start + health-check + stop the REAL
embedding model from the downloaded file, then re-run the download and
assert the duplicate path (one row, refreshed).
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import initialize_database
from controlgauge_service.db.models import ModelFile
from controlgauge_service.model_catalog.manifest import load_catalog
from controlgauge_service.model_runtime.download import DownloadManager
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from tests.conftest import MODEL_ENV, VALID_TOKEN

pytestmark = [
    pytest.mark.skipif(
        not MODEL_ENV.ready or os.environ.get("CONTROLGAUGE_TEST_ALLOW_DOWNLOAD") != "1",
        reason=(
            "real-network download integration; needs the dev llama-server "
            "and CONTROLGAUGE_TEST_ALLOW_DOWNLOAD=1"
        ),
    ),
]

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ALLOWED_SUFFIXES = ("huggingface.co", "hf.co")
_NOMIC_ID = "nomic-embed-text-v1.5-q4-k-m"


@pytest.fixture(scope="module", autouse=True)
def _connect_auditor() -> Iterator[None]:
    """Allow only loopback and HF-family hosts (by resolved IP): the
    real-network form of the offline guard, scoped to exactly the hosts
    this gate exists for."""
    resolved_allowed: set[str] = set()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def audited_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        infos = real_getaddrinfo(host, *args, **kwargs)
        if isinstance(host, str) and host.endswith(_ALLOWED_SUFFIXES):
            for info in infos:
                resolved_allowed.add(info[4][0])
        return infos

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address and isinstance(address[0], str):
            host = address[0]
            if host in _LOOPBACK_HOSTS or host in resolved_allowed:
                return real_connect(sock, address)
        raise AssertionError(f"non-allowlisted connection attempted: {address!r}")

    patcher = pytest.MonkeyPatch()
    patcher.setattr(socket, "getaddrinfo", audited_getaddrinfo)
    patcher.setattr(socket.socket, "connect", guarded_connect)
    yield
    patcher.undo()


class _DownloadFlow:
    def __init__(self, client: TestClient, database: Any, downloads: DownloadManager) -> None:
        self.client = client
        self.database = database
        self.downloads = downloads
        self.headers = {"Authorization": f"Bearer {VALID_TOKEN}"}


@pytest.fixture(scope="module")
def download_flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_DownloadFlow]:
    root = tmp_path_factory.mktemp("sprint6-download")
    database = initialize_database(root)
    manager = ModelRuntimeManager(MODEL_ENV.server)
    downloads = DownloadManager(database)
    client = TestClient(
        create_app(
            Settings(session_token=VALID_TOKEN, llama_server=MODEL_ENV.server, model_dir=root),
            database=database,
            model_runtime=manager,
            downloads=downloads,
        )
    )
    try:
        yield _DownloadFlow(client, database, downloads)
    finally:
        manager.stop_all()
        downloads.shutdown()
        client.close()
        database.dispose()


def _poll_terminal(flow: _DownloadFlow, download_id: str, timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = flow.client.get(f"/model-catalog/downloads/{download_id}", headers=flow.headers)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["state"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.2)
    pytest.fail("the real download did not reach a terminal state")


def _nomic_rows(flow: _DownloadFlow) -> list[Any]:
    with flow.database.session() as session:
        rows: list[Any] = list(session.scalars(select(ModelFile)).all())
    return rows


def test_integration_download_nomic_end_to_end(download_flow: _DownloadFlow) -> None:
    flow = download_flow
    client = flow.client
    entry = next(
        candidate for candidate in load_catalog().entries if candidate.model_id == _NOMIC_ID
    )
    manifest_sha = entry.files[0].sha256

    # 0) Structural gate: nothing downloads without the approval POST.
    assert flow.downloads.active() is None

    # 1) THE approval: the authenticated POST starts the real download.
    started = client.post(f"/model-catalog/{_NOMIC_ID}/download", headers=flow.headers)
    assert started.status_code == 202, started.text
    download_id: str = started.json()["download_id"]

    # 2) The real 84 MB exchange, polled structurally to completion.
    status = _poll_terminal(flow, download_id)
    assert status["state"] == "completed", status
    assert status["error_code"] is None

    # 3) The registered row IS the manifest pin, byte for byte.
    rows = _nomic_rows(flow)
    assert len(rows) == 1
    assert rows[0].sha256_hash == manifest_sha
    assert rows[0].size_bytes == entry.files[0].size_bytes

    # 4) The downloaded file runs: start -> ready -> health -> stop.
    model_id = rows[0].id
    started_model = client.post(f"/models/{model_id}/start", json={}, headers=flow.headers)
    assert started_model.status_code == 200, started_model.text
    assert started_model.json()["state"] == "ready"
    health = client.post(f"/models/{model_id}/health", headers=flow.headers)
    assert health.status_code == 200, health.text
    assert health.json()["state"] == "ready"
    stopped = client.post(f"/models/{model_id}/stop", json={}, headers=flow.headers)
    assert stopped.status_code == 200, stopped.text

    # 5) The catalog's installed state reflects the download (single-file
    # entry: the interim sha-match lookup is exact).
    catalog = client.get("/model-catalog", headers=flow.headers)
    assert catalog.status_code == 200, catalog.text
    nomic = next(item for item in catalog.json()["entries"] if item["model_id"] == _NOMIC_ID)
    assert nomic["installed"] is True
    assert nomic["installed_model_ids"] == [model_id]

    # 6) Re-run the download: the duplicate path (one row, refreshed).
    rerun = client.post(f"/model-catalog/{_NOMIC_ID}/download", headers=flow.headers)
    assert rerun.status_code == 202, rerun.text
    rerun_status = _poll_terminal(flow, rerun.json()["download_id"])
    assert rerun_status["state"] == "completed", rerun_status
    rows_after = _nomic_rows(flow)
    assert len(rows_after) == 1
    assert rows_after[0].id == model_id
    assert rows_after[0].last_verified_at is not None
