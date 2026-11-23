"""S6-05: the external-endpoint registry routes over TestClient + a stub
loopback OpenAI-compatible server.

The stub (an `http.server.ThreadingHTTPServer` on 127.0.0.1) implements
the three endpoints a local inference server needs: `POST
/v1/chat/completions`, `POST /v1/embeddings`, `GET /v1/models`. No child
is ever spawned; the whole module runs under the `offline_guard` (every
connection attempt must be loopback), which doubles as the proof that
the registry surface cannot reach beyond loopback.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.db import initialize_database
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.model_runtime.protocols import HealthStatus, ModelDescriptor

from .conftest import VALID_TOKEN

pytestmark = [pytest.mark.usefixtures("offline_guard")]

AUTH = {"Authorization": f"Bearer {VALID_TOKEN}"}

_GEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
    "additionalProperties": False,
}


# --- the OpenAI-compatible stub ---------------------------------------------------


@dataclass
class _EndpointStubState:
    """Per-server behavior the tests arrange."""

    chat_status: int = 200
    #: The completion's message content (a JSON string, as a real server
    #: would emit for a constrained generation).
    chat_content: str = '{"ok": true}'
    embed_status: int = 200
    embed_vector: list[float] = field(default_factory=lambda: [0.25, -0.5, 0.75])
    completions_seen: list[dict[str, Any]] = field(default_factory=list)
    embeddings_seen: list[dict[str, Any]] = field(default_factory=list)


class _EndpointStubHandler(http.server.BaseHTTPRequestHandler):
    server_version = "EndpointStub/1"

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
        state: _EndpointStubState = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path == "/v1/chat/completions":
            state.completions_seen.append(self._read_json())
            if state.chat_status != 200:
                self._send_json(state.chat_status, {"error": "stub failure"})
                return
            self._send_json(
                state.chat_status,
                {
                    "choices": [{"message": {"role": "assistant", "content": state.chat_content}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )
            return
        if path == "/v1/embeddings":
            state.embeddings_seen.append(self._read_json())
            if state.embed_status != 200:
                self._send_json(state.embed_status, {"error": "stub failure"})
                return
            self._send_json(
                state.embed_status, {"data": [{"embedding": state.embed_vector, "index": 0}]}
            )
            return
        self._send_json(404, {"error": "not found"})


class _EndpointStubServer(http.server.ThreadingHTTPServer):
    state: _EndpointStubState


@pytest.fixture()
def endpoint_stub() -> Any:
    server = _EndpointStubServer(("127.0.0.1", 0), _EndpointStubHandler)
    server.state = _EndpointStubState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)


def _base_url(server: Any) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture()
def endpoints_env(tmp_path: Any) -> Any:
    """App + client + database + the real (empty) manager."""
    database = initialize_database(tmp_path)
    manager = ModelRuntimeManager(tmp_path / "llama-server")
    app = create_app(
        Settings(session_token=VALID_TOKEN),
        database=database,
        model_runtime=manager,
    )
    client = TestClient(app)
    yield SimpleNamespace(client=client, database=database, manager=manager, tmp_path=tmp_path)
    client.close()
    database.dispose()


def _register(
    client: TestClient,
    base_url: str,
    *,
    display_name: str = "Local llama-server",
    backend: str = "llama_cpp",
    capabilities: str = "generation",
) -> Any:
    return client.post(
        "/model-endpoints",
        json={
            "display_name": display_name,
            "backend": backend,
            "base_url": base_url,
            "capabilities": capabilities,
        },
        headers=AUTH,
    )


# --- auth -------------------------------------------------------------------------


def test_endpoint_routes_require_auth(endpoints_env: Any) -> None:
    client = endpoints_env.client
    assert client.post("/model-endpoints", json={}).status_code == 401
    assert client.get("/model-endpoints").status_code == 401
    assert client.delete("/model-endpoints/some-id").status_code == 401
    assert client.post("/model-endpoints/some-id/health").status_code == 401
    attach = client.post("/model-endpoints/some-id/attach", json={"role": "generation"})
    assert attach.status_code == 401


# --- registration: validation -----------------------------------------------------


def test_register_requires_loopback(endpoints_env: Any) -> None:
    client = endpoints_env.client
    for url in ("http://10.0.0.5:8080", "https://example.com"):
        response = _register(client, url)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "registry_endpoint_not_loopback"
    assert client.get("/model-endpoints", headers=AUTH).json()["endpoints"] == []


def test_register_refuses_malformed_urls(endpoints_env: Any) -> None:
    client = endpoints_env.client
    for url in (
        "not-a-url",
        "ftp://127.0.0.1:8080",  # loopback host, wrong scheme
        "https://127.0.0.1:8080",  # loopback host, https is not accepted
        "http://127.0.0.1:8080/v1",  # a path would be stored and echoed
        "http://user:secret@127.0.0.1:8080",  # credentials are never stored
        "http://127.0.0.1:8080/?x=1",  # query refused
        "http://127.0.0.1:8080/#frag",  # fragment refused
        "",  # empty
        "x" * 256,  # over the bound
    ):
        response = _register(client, url)
        assert response.status_code == 422, f"{url!r}: {response.text}"
        assert response.json()["error"]["code"] == "registry_endpoint_invalid"
    assert client.get("/model-endpoints", headers=AUTH).json()["endpoints"] == []


def test_register_refuses_unknown_fields_and_bad_names(endpoints_env: Any) -> None:
    client = endpoints_env.client
    response = client.post(
        "/model-endpoints",
        json={
            "display_name": "Local",
            "backend": "llama_cpp",
            "base_url": "http://127.0.0.1:9",
            "capabilities": "generation",
            "credential": "nope",
        },
        headers=AUTH,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    bad_name = _register(client, "http://127.0.0.1:9", display_name="bad\nname")
    assert bad_name.status_code == 422
    assert bad_name.json()["error"]["code"] == "registry_endpoint_invalid"


# --- registration: probe-gated persistence ----------------------------------------


def test_register_happy_path(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    response = _register(client, _base_url(endpoint_stub))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["display_name"] == "Local llama-server"
    assert body["backend"] == "llama_cpp"
    # Normalized scheme://host:port -- no path, no query, nothing else.
    assert body["base_url_host_only"] == _base_url(endpoint_stub)
    assert body["capabilities"] == "generation"
    assert body["last_health_state"] == "ready"
    assert body["last_health_code"] is None
    assert body["last_health_at"] is not None
    # The probe sent the fixed D-023 health generation.
    assert endpoint_stub.state.completions_seen, "expected the bounded health generation"
    assert body["id"]


def test_register_normalizes_url(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    response = _register(client, _base_url(endpoint_stub) + "/")
    assert response.status_code == 201, response.text
    assert response.json()["base_url_host_only"] == _base_url(endpoint_stub)


def test_register_unreachable_refused_and_not_persisted(endpoints_env: Any) -> None:
    client = endpoints_env.client
    # A loopback port with nothing listening (bind, then close).
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    response = _register(client, f"http://127.0.0.1:{port}")
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "registry_endpoint_unreachable"
    # An endpoint we cannot verify is not registered.
    assert client.get("/model-endpoints", headers=AUTH).json()["endpoints"] == []


def test_register_both_requires_every_capability(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    # Embedding half fails -> 502 and NO row (a half-working endpoint is
    # not ready; different capabilities from the later registration, so
    # the duplicate gate does not pre-empt the probe).
    endpoint_stub.state.embed_status = 500
    refused = _register(client, _base_url(endpoint_stub), capabilities="both")
    assert refused.status_code == 502, refused.text
    assert client.get("/model-endpoints", headers=AUTH).json()["endpoints"] == []
    # Both pass -> registered.
    endpoint_stub.state.embed_status = 200
    ok = _register(client, _base_url(endpoint_stub), capabilities="both")
    assert ok.status_code == 201, ok.text
    assert ok.json()["capabilities"] == "both"
    listed = client.get("/model-endpoints", headers=AUTH).json()["endpoints"]
    assert len(listed) == 1


def test_register_embedding_only_probes_embeddings(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    response = _register(client, _base_url(endpoint_stub), capabilities="embedding")
    assert response.status_code == 201, response.text
    assert endpoint_stub.state.embeddings_seen, "expected the bounded health embed"
    assert endpoint_stub.state.completions_seen == []


def test_register_duplicate_conflict(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    first = _register(client, _base_url(endpoint_stub))
    assert first.status_code == 201
    again = _register(client, _base_url(endpoint_stub))
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "registry_duplicate"
    # A different capability set is a different registration.
    other = _register(client, _base_url(endpoint_stub), capabilities="embedding")
    assert other.status_code == 201


# --- listing (structural) ---------------------------------------------------------


def test_list_is_structural(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    assert _register(client, _base_url(endpoint_stub)).status_code == 201
    # The recorded health stands even when the endpoint has since died --
    # the list NEVER probes.
    endpoint_stub.shutdown()
    listed = client.get("/model-endpoints", headers=AUTH).json()["endpoints"]
    assert len(listed) == 1
    assert listed[0]["last_health_state"] == "ready"


# --- live health ------------------------------------------------------------------


def test_health_route_records_outcomes(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    endpoint_id = _register(client, _base_url(endpoint_stub)).json()["id"]

    # Healthy: the probe passes, `ready` is recorded.
    healthy = client.post(f"/model-endpoints/{endpoint_id}/health", headers=AUTH)
    assert healthy.status_code == 200
    assert healthy.json()["last_health_state"] == "ready"
    assert healthy.json()["last_health_code"] is None

    # Misbehaving: answers 500 -> server_error -> unhealthy.
    endpoint_stub.state.chat_status = 500
    unhealthy = client.post(f"/model-endpoints/{endpoint_id}/health", headers=AUTH)
    assert unhealthy.status_code == 200
    assert unhealthy.json()["last_health_state"] == "unhealthy"
    assert unhealthy.json()["last_health_code"] == "server_error"

    # Gone: connection refused -> unreachable. `shutdown()` alone stops
    # the serve loop but leaves the listening socket open (kernel would
    # accept and starve the request into a timeout); `server_close()`
    # closes it so connects are truly refused.
    endpoint_stub.shutdown()
    endpoint_stub.server_close()
    unreachable = client.post(f"/model-endpoints/{endpoint_id}/health", headers=AUTH)
    assert unreachable.status_code == 200
    assert unreachable.json()["last_health_state"] == "unreachable"
    assert unreachable.json()["last_health_code"] == "transport_error"


def test_health_unknown_endpoint(endpoints_env: Any) -> None:
    response = endpoints_env.client.post(
        "/model-endpoints/00000000-0000-0000-0000-000000000000/health", headers=AUTH
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "endpoint_not_found"


# --- attach / use / detach / delete -----------------------------------------------


def test_attach_generate_detach_delete(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    manager = endpoints_env.manager
    endpoint_id = _register(client, _base_url(endpoint_stub)).json()["id"]

    attached = client.post(
        f"/model-endpoints/{endpoint_id}/attach", json={"role": "generation"}, headers=AUTH
    )
    assert attached.status_code == 200, attached.text
    assert attached.json() == {"role": "generation", "state": "ready", "model_id": endpoint_id}

    # Structural: GET /models shows the endpoint id as the role occupant.
    inventory = client.get("/models", headers=AUTH).json()
    assert inventory["runtime"]["generation"]["model_id"] == endpoint_id
    assert inventory["runtime"]["generation"]["state"] == "ready"

    # Real traffic: one structured generation through the attached provider.
    provider = manager.get("generation")
    assert provider is not None
    endpoint_stub.state.chat_content = '{"answer": "forty-two"}'
    generation = provider.generate("Give an answer.", _GEN_SCHEMA, timeout_s=5.0)
    assert generation.data == {"answer": "forty-two"}
    assert generation.usage is not None
    assert generation.usage.prompt_tokens == 5
    # The provider sent the minimal payload: schema rides response_format.
    sent = endpoint_stub.state.completions_seen[-1]
    assert sent["response_format"]["json_schema"]["schema"] == _GEN_SCHEMA
    assert sent["stream"] is False
    assert "temperature" not in sent  # sampling belongs to the endpoint

    # Delete while attached is refused.
    delete_refused = client.delete(f"/model-endpoints/{endpoint_id}", headers=AUTH)
    assert delete_refused.status_code == 409
    assert delete_refused.json()["error"]["code"] == "endpoint_attached"

    # Detach = the role's stop route, addressed by the ENDPOINT id.
    detached = client.post(f"/models/{endpoint_id}/stop", headers=AUTH)
    assert detached.status_code == 200, detached.text
    assert detached.json()["state"] == "stopped"
    assert client.get("/models", headers=AUTH).json()["runtime"]["generation"]["model_id"] is None

    # Now the delete succeeds.
    deleted = client.delete(f"/model-endpoints/{endpoint_id}", headers=AUTH)
    assert deleted.status_code == 200
    assert client.get("/model-endpoints", headers=AUTH).json()["endpoints"] == []


def test_attach_unknown_endpoint(endpoints_env: Any) -> None:
    response = endpoints_env.client.post(
        "/model-endpoints/00000000-0000-0000-0000-000000000000/attach",
        json={"role": "generation"},
        headers=AUTH,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "endpoint_not_found"


def test_attach_refuses_role_beyond_capabilities(endpoints_env: Any, endpoint_stub: Any) -> None:
    client = endpoints_env.client
    endpoint_id = _register(client, _base_url(endpoint_stub), capabilities="embedding").json()["id"]
    response = client.post(
        f"/model-endpoints/{endpoint_id}/attach", json={"role": "generation"}, headers=AUTH
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["fields"][0]["field"] == "role"


def test_attach_switches_occupants(endpoints_env: Any, endpoint_stub: Any, tmp_path: Any) -> None:
    """The switch contract holds for attach too: attaching over an
    occupied role stops the previous occupant."""
    client = endpoints_env.client
    manager = endpoints_env.manager

    class _Fake:
        def __init__(self) -> None:
            self.stopped = False

        @property
        def model_id(self) -> str:
            return "file-1"

        def stop(self) -> None:
            self.stopped = True

        def health_check(self) -> HealthStatus:
            return HealthStatus(state="ready", checked_at=datetime.now(UTC))

        def describe(self) -> ModelDescriptor:
            return ModelDescriptor(
                model_id="file-1", display_name="file-1", role="generation", context_tokens=1
            )

    fake = _Fake()
    manager.attach("generation", fake, display_name="file-1")
    endpoint_id = _register(client, _base_url(endpoint_stub)).json()["id"]
    attached = client.post(
        f"/model-endpoints/{endpoint_id}/attach", json={"role": "generation"}, headers=AUTH
    )
    assert attached.status_code == 200
    assert fake.stopped, "the switch must stop the previous occupant"
    assert manager.loaded_model_id("generation") == endpoint_id


def test_delete_unknown_endpoint(endpoints_env: Any) -> None:
    response = endpoints_env.client.delete(
        "/model-endpoints/00000000-0000-0000-0000-000000000000", headers=AUTH
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "endpoint_not_found"
