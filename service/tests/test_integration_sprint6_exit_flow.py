"""S6-08: the Sprint 6 exit-validation flow -- one continuous evidence
trail in-process, under the offline guard.

Composes the Sprint 6 surface end to end AFTER the download step (the
real-network download E2E stays in ``test_download_integration.py``;
this module never contacts anything but loopback plus the local
detection probes):

    fresh app -> GET /hardware (real detection) -> GET /recommendations
    (detected hardware) -> the three shipped profile routes -> the
    catalog embedding model registered against its manifest pin ->
    start -> real embed health -> stop -> external endpoint
    register/attach/generate/stop against a loopback stub server.

The "downloaded model" is represented by the dev embedding file, which
the import below proves byte-identical to the catalog download by
ENFORCING the manifest's pinned SHA-256 (``expected_sha256`` -- a
mismatch is a 422, so a passing run IS the content-identity evidence;
the download path itself, ``origin=catalog_download``, is proven live by
the S6-04 module). The external endpoint is a loopback stub (the S6-05
unit pattern); the real-engine round trip lives in
``test_model_integration.py``.

Skips cleanly everywhere the dev environment is not configured (Windows
CI never sets it); collected everywhere. Every test runs under the
``offline_guard``: any non-loopback connection attempt fails the test,
so a passing run is itself the offline evidence. Evidence lines print to
the test log (``pytest -s``) and are recorded in ``docs/DEV_MODELS.md``
§9.
"""

from __future__ import annotations

import http.server
import json
import math
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import initialize_database
from controlgauge_service.model_catalog.manifest import load_catalog
from controlgauge_service.model_catalog.recommend import (
    ENTRY_VERDICTS,
    LOAD_MODES,
    PAIR_VERDICTS,
    REASON_CODES,
)
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.model_runtime.protocols import (
    EmbeddingCapable,
    GenerationCapable,
)
from tests.conftest import EXPECTED_EMBEDDING_DIMENSIONS, MODEL_ENV, VALID_TOKEN

pytestmark = [
    pytest.mark.skipif(
        not MODEL_ENV.ready,
        reason="real llama-server binary and/or dev models not configured",
    ),
    # Offline evidence: the whole flow must run without any non-loopback
    # connection (the detection probes are local; the endpoint stub is
    # loopback by construction).
    pytest.mark.usefixtures("offline_guard"),
]

_NOMIC_ID = "nomic-embed-text-v1.5-q4-k-m"
_PROFILE_IDS = ("workstation-32gb", "enterprise-288gb", "consumer-cpu")

_OK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}
_OK_PROMPT = 'Reply with the JSON object {"ok": true}.'


@pytest.fixture(autouse=True)
def _isolated_data_dir(
    _isolated_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """An isolated data dir for the hardware cache (the documented
    ``CONTROLGAUGE_DATA_DIR`` dev/test override), resolved by the routes
    at request time. Depends on the conftest strip so the override is
    applied AFTER ambient ``CONTROLGAUGE_*`` variables are removed."""
    monkeypatch.setenv("CONTROLGAUGE_DATA_DIR", str(tmp_path))
    return tmp_path


class _ExitFlow:
    """The S6-08 harness: one fresh app (real database + real
    per-capability manager + the real dev binary) shared by the module's
    tests -- the continuous-evidence property the spec asks for."""

    def __init__(
        self,
        client: TestClient,
        manager: ModelRuntimeManager,
        headers: dict[str, str],
    ) -> None:
        self.client = client
        self.manager = manager
        self.headers = headers


@pytest.fixture(scope="module")
def exit_flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ExitFlow]:
    root = tmp_path_factory.mktemp("sprint6-exit-flow")
    database = initialize_database(root)
    manager = ModelRuntimeManager(MODEL_ENV.server)
    client = TestClient(
        create_app(
            Settings(session_token=VALID_TOKEN, llama_server=MODEL_ENV.server, model_dir=root),
            database=database,
            model_runtime=manager,
        )
    )
    try:
        yield _ExitFlow(client, manager, {"Authorization": f"Bearer {VALID_TOKEN}"})
    finally:
        # No llama-server child may outlive the module, whatever a test
        # left running on the way out.
        manager.stop_all()
        client.close()
        database.dispose()


# --- evidence formatting ------------------------------------------------------


def _reasons(values: list[str]) -> str:
    return ",".join(values) if values else "-"


def _verdict_table(report: dict[str, Any]) -> str:
    """One aligned report table for the test log (the S6-08 evidence
    recorded in ``docs/DEV_MODELS.md`` §9)."""
    lines = [f"hardware_source: {report['hardware_source']}", "  entries:"]
    for verdict in report["entries"]:
        load_mode = verdict["load_mode"] or "-"
        lines.append(
            f"    {verdict['model_id']:<34} {verdict['role']:<10} "
            f"{verdict['verdict']:<12} load={load_mode:<13} "
            f"total={verdict['estimated']['total_bytes']:>12} {_reasons(verdict['reasons'])}"
        )
    lines.append("  pairs:")
    for pair in report["pairs"]:
        lines.append(
            f"    {pair['generation_model_id']:<34} x {pair['embedding_model_id']:<34} "
            f"{pair['verdict']:<15} combined={pair['combined_total_bytes']:>12} "
            f"{_reasons(pair['reasons'])}"
        )
    return "\n".join(lines)


# --- exit criterion 1+2: detection -> recommendations ----------------------------


def test_exit_flow_hardware_then_recommendations(exit_flow: _ExitFlow, tmp_path: Path) -> None:
    """Exit criteria 1 and 2 in one continuous trail: a fresh app detects
    the REAL hardware (cold ``GET /hardware`` -- on this box the real
    ``nvidia-smi`` path enumerating the three RTX PRO 6000s), the
    detected report is computed from the cached snapshot, and the three
    shipped profiles answer the same math synthetically -- the enterprise
    profile recommending the FULL catalog (the honest no-ceiling
    property) and consumer-cpu never auto-promoting the 14B entry."""
    flow = exit_flow
    catalog_entries = load_catalog().entries
    expected_entry_count = sum(len(entry.roles) for entry in catalog_entries)
    generation_entries = [e for e in catalog_entries if "generation" in e.roles]
    embedding_entries = [e for e in catalog_entries if "embedding" in e.roles]

    # 1) Cold GET /hardware: one real, bounded, local detection pass.
    hardware = flow.client.get("/hardware", headers=flow.headers)
    assert hardware.status_code == 200, hardware.text
    hw_body: dict[str, Any] = hardware.json()
    assert hw_body["os_name"] in {"windows", "linux", "macos", "unknown"}
    # On a configured integration box the RAM/core probes cannot be broken.
    assert hw_body["ram_total_bytes"] is not None
    assert hw_body["cpu_cores_logical"] is not None
    assert isinstance(hw_body["gpus"], list)
    for gpu in hw_body["gpus"]:
        assert set(gpu) == {"vendor", "name", "vram_bytes"}
        assert gpu["vendor"] in {"nvidia", "amd", "intel", "unknown"}
    # T-10: machine facts only -- never either local path.
    serialized = json.dumps(hw_body)
    assert str(tmp_path) not in serialized

    print("=== S6-08 evidence: GET /hardware (this box) ===")
    print(json.dumps(hw_body, indent=2))

    # 2) The detected report, computed from the cached snapshot (never a
    #    live re-detect -- the cache the GET above just stored).
    detected = flow.client.get("/recommendations", headers=flow.headers)
    assert detected.status_code == 200, detected.text
    detected_body: dict[str, Any] = detected.json()
    assert detected_body["hardware_source"] == "detected"
    assert len(detected_body["entries"]) == expected_entry_count
    for verdict in detected_body["entries"]:
        assert verdict["verdict"] in ENTRY_VERDICTS
        assert set(verdict["reasons"]) <= set(REASON_CODES)
        assert verdict["estimated"]["total_bytes"] > 0
        if verdict["role"] == "generation":
            assert verdict["load_mode"] is None or verdict["load_mode"] in LOAD_MODES
    assert len(detected_body["pairs"]) == len(generation_entries) * len(embedding_entries)
    totals = {v["model_id"]: v["estimated"]["total_bytes"] for v in detected_body["entries"]}
    for pair in detected_body["pairs"]:
        assert pair["verdict"] in PAIR_VERDICTS
        assert set(pair["reasons"]) <= set(REASON_CODES)
        assert (
            pair["combined_total_bytes"]
            == totals[pair["generation_model_id"]] + totals[pair["embedding_model_id"]]
        )

    print("=== S6-08 evidence: GET /recommendations (detected hardware) ===")
    print(_verdict_table(detected_body))

    # 3) The three shipped profiles: the same math on synthetic hardware.
    reports: dict[str, dict[str, Any]] = {}
    for profile_id in _PROFILE_IDS:
        response = flow.client.get(f"/recommendations/profile/{profile_id}", headers=flow.headers)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert body["hardware_source"] == f"profile:{profile_id}"
        assert len(body["entries"]) == expected_entry_count
        assert len(body["pairs"]) == len(generation_entries) * len(embedding_entries)
        reports[profile_id] = body
        print(f"=== S6-08 evidence: GET /recommendations/profile/{profile_id} ===")
        print(_verdict_table(body))

    # Footprint estimates are hardware-independent: identical for the
    # same entry across every hardware source.
    detected_estimates = {v["model_id"]: v["estimated"] for v in detected_body["entries"]}
    for body in reports.values():
        assert {v["model_id"]: v["estimated"] for v in body["entries"]} == detected_estimates

    # The honest no-ceiling property: the enterprise profile recommends
    # the FULL shipped catalog (288 GB aggregate -- nothing is withheld
    # by hardware, only ever by the curator gate).
    enterprise = reports["enterprise-288gb"]
    assert all(verdict["verdict"] == "recommended" for verdict in enterprise["entries"])

    # The 32 GB pro target is satisfiable by the shipped catalog.
    workstation = reports["workstation-32gb"]
    assert all(
        verdict["verdict"] in {"recommended", "feasible"} for verdict in workstation["entries"]
    )

    # The honest direction on the smallest profile: the 14B entry is
    # never auto-promoted there, while the FAST tier serves it.
    consumer_verdicts = {
        verdict["model_id"]: verdict["verdict"] for verdict in reports["consumer-cpu"]["entries"]
    }
    assert consumer_verdicts["qwen2.5-14b-instruct-q4-k-m"] != "recommended"
    assert consumer_verdicts["qwen2.5-1.5b-instruct-q4-k-m"] == "recommended"


# --- exit criterion 3 (runtime half): the catalog model runs ---------------------


def test_exit_flow_catalog_model_start_health_stop(exit_flow: _ExitFlow) -> None:
    """The runtime half of the download flow, offline: the catalog
    embedding entry's content is registered through the import route with
    the manifest's pinned SHA-256 ENFORCED (a passing run proves the dev
    file is byte-identical to the catalog download), the registry
    distinction is visible on the record and in the catalog's installed
    state, the model starts under the real manager, its health check is a
    REAL embed (D-023) with the pinned dimensionality, and the stop
    leaves the role empty."""
    flow = exit_flow
    entry = next(e for e in load_catalog().entries if e.model_id == _NOMIC_ID)
    pin = entry.files[0].sha256

    imported = flow.client.post(
        "/models/import",
        json={
            "path": str(MODEL_ENV.embedding),
            "role": "embedding",
            "expected_sha256": pin,
        },
        headers=flow.headers,
    )
    assert imported.status_code == 201, imported.text
    body: dict[str, Any] = imported.json()
    assert body["provenance"] == "user_checksum"
    assert body["model"]["sha256"] == pin
    assert body["model"]["size_bytes"] == entry.files[0].size_bytes
    assert body["model"]["origin"] == "local_import"
    assert body["model"]["verification"] == "user_checksum"
    model_id: str = body["model"]["id"]

    # The registry installed-state lookup (S6-05 sha coverage) now covers
    # the catalog entry: the registered content IS the shipped entry's file.
    catalog = flow.client.get("/model-catalog", headers=flow.headers)
    assert catalog.status_code == 200, catalog.text
    nomic = next(item for item in catalog.json()["entries"] if item["model_id"] == _NOMIC_ID)
    assert nomic["installed"] is True
    assert nomic["installed_model_ids"] == [model_id]

    started = flow.client.post(f"/models/{model_id}/start", json={}, headers=flow.headers)
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "ready"

    health = flow.client.post(f"/models/{model_id}/health", headers=flow.headers)
    assert health.status_code == 200, health.text
    assert health.json()["state"] == "ready"  # a real embed runs behind it (D-023)

    # The embed itself: finite, and the conftest-pinned dimensionality.
    provider = cast(EmbeddingCapable, flow.manager.get("embedding"))
    assert provider is not None
    vector = provider.embed("sprint 6 exit-flow embed probe")
    assert len(vector) == EXPECTED_EMBEDDING_DIMENSIONS
    assert all(math.isfinite(value) for value in vector)

    stopped = flow.client.post(f"/models/{model_id}/stop", json={}, headers=flow.headers)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["state"] == "stopped"
    after = flow.client.post(f"/models/{model_id}/health", headers=flow.headers)
    assert after.status_code == 200, after.text
    assert after.json()["state"] == "stopped"
    assert after.json()["model_id"] is None

    print("=== S6-08 evidence: catalog-model flow ===")
    print(
        f"import(expected_sha256={pin[:12]}...) verified -> catalog installed=true -> "
        f"start=ready -> health=ready (real embed, {EXPECTED_EMBEDDING_DIMENSIONS}-dim) -> "
        "stop=stopped"
    )


# --- the external-endpoint trail (S6-05 surface, loopback stub) -------------------


@dataclass
class _StubState:
    """What the stub records; the content passes the fixed health schema."""

    chat_content: str = '{"ok": true}'
    completions_seen: int = 0


class _StubHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ExitFlowStub/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # never log request lines (they carry request content)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] == "/v1/models":
            self._send_json(200, {"object": "list", "data": []})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        state: _StubState = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path == "/v1/chat/completions":
            self._read_json()
            state.completions_seen += 1
            self._send_json(
                200,
                {
                    "choices": [{"message": {"role": "assistant", "content": state.chat_content}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )
            return
        self._send_json(404, {"error": "not found"})


class _StubServer(http.server.ThreadingHTTPServer):
    state: _StubState


@pytest.fixture()
def endpoint_stub() -> Iterator[Any]:
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    server.state = _StubState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


def test_exit_flow_external_endpoint_register_attach_generate_stop(
    exit_flow: _ExitFlow, endpoint_stub: Any
) -> None:
    """The second provider kind closes the trail: a user-registered
    LOOPBACK endpoint (the stub stands in for any local
    OpenAI-compatible server; the real-engine round trip is S6-05's
    integration test) registers through the probe-gated route (the probe
    is a REAL bounded generation), attaches as the generation role,
    serves one schema-validated structured generation through the
    manager, health-checks ready, and detaches via the role stop route
    -- which closes our client but never touches the endpoint itself."""
    flow = exit_flow
    base_url = f"http://127.0.0.1:{endpoint_stub.server_address[1]}"

    registered = flow.client.post(
        "/model-endpoints",
        headers=flow.headers,
        json={
            "display_name": "Exit-flow endpoint",
            "backend": "llama_cpp",
            "base_url": base_url,
            "capabilities": "generation",
        },
    )
    assert registered.status_code == 201, registered.text
    registered_body: dict[str, Any] = registered.json()
    endpoint_id: str = registered_body["id"]
    # The registration probe was a REAL generation against the stub.
    assert registered_body["last_health_state"] == "ready"
    probe_count: int = endpoint_stub.state.completions_seen
    assert probe_count >= 1

    attached = flow.client.post(
        f"/model-endpoints/{endpoint_id}/attach",
        headers=flow.headers,
        json={"role": "generation"},
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["model_id"] == endpoint_id

    inventory = flow.client.get("/models", headers=flow.headers)
    assert inventory.status_code == 200, inventory.text
    runtime = inventory.json()["runtime"]
    assert runtime["generation"]["state"] == "ready"
    assert runtime["generation"]["model_id"] == endpoint_id

    provider = cast(GenerationCapable, flow.manager.get("generation"))
    assert provider is not None
    generation = provider.generate(_OK_PROMPT, _OK_SCHEMA, timeout_s=60.0)
    assert generation.data == {"ok": True}

    health = flow.client.post(f"/model-endpoints/{endpoint_id}/health", headers=flow.headers)
    assert health.status_code == 200, health.text
    assert health.json()["last_health_state"] == "ready"

    stopped = flow.client.post(f"/models/{endpoint_id}/stop", headers=flow.headers)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["state"] == "stopped"
    after = flow.client.get("/models", headers=flow.headers)
    assert after.json()["runtime"]["generation"]["model_id"] is None
    # Detach closed OUR client only; the endpoint is not ours to kill and
    # the detach health probes are visible in the stub's request count.
    assert endpoint_stub.state.completions_seen > probe_count

    print("=== S6-08 evidence: external-endpoint flow ===")
    print(
        "register(probe=ready, real generation) -> attach(generation) -> "
        "structured generation ok -> health=ready -> stop=detached"
    )
