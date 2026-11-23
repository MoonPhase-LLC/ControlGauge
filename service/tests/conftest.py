from __future__ import annotations

import http.server
import os
import shutil
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings

#: D-028: the Linux-only containment tests are not collected at all off
#: Linux (not even as skips), so the Windows suite counts stay exactly as
#: they were before the Linux sandbox existed. Symmetrically, the
#: Windows-Job-Object backend tests are not collected off Windows.
collect_ignore_glob = [
    *(
        []
        if sys.platform == "linux"
        else ["test_containment_linux.py", "test_model_childproc_linux.py"]
    ),
    *([] if sys.platform == "win32" else ["test_model_childproc_windows.py"]),
]

#: Sprint 5 (PD-023): real-model integration tests skip cleanly unless
#: the dev binary AND both dev models are configured AND their sizes
#: match the pinned expectations below (a partially-downloaded file is
#: refused, so a truncated download can't produce confusing failures).
#: Dev-model byte sizes MAY live here per PD-023 (docs/tests are the only
#: sanctioned homes for dev-model identity). Read at conftest import time
#: -- before any test's `_isolated_environment` fixture strips ambient
#: `CONTROLGAUGE_*` variables.
_MODEL_ENV_VARS = SimpleNamespace(
    server="CONTROLGAUGE_TEST_LLAMA_SERVER",
    generation="CONTROLGAUGE_TEST_MODEL_GENERATION",
    embedding="CONTROLGAUGE_TEST_MODEL_EMBEDDING",
)

#: Expected exact byte sizes: the generation env var points at the FIRST
#: shard of the split GGUF; the embedding model is a single file.
_EXPECTED_TEST_MODEL_SIZES = {
    "generation": 3_993_201_344,
    "embedding": 84_106_624,
}

#: Expected embedding dimensionality (nomic-embed-text-v1.5 model card).
#: The integration tests assert the SERVER-observed dimension against this
#: constant -- on mismatch the test fails and ``docs/DEV_MODELS.md`` is
#: corrected (the "verify, don't assert blindly" loop, S5-04). Server-
#: observed 768 on the dev box, 2026-11-17.
EXPECTED_EMBEDDING_DIMENSIONS = 768


def _load_model_env() -> SimpleNamespace:
    """Resolve the integration-test environment once per session. Paths
    must exist, be regular files, and match the pinned sizes."""

    def resolved(name: str) -> Path | None:
        raw = os.environ.get(name)
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_file() else None

    server = resolved(_MODEL_ENV_VARS.server)
    generation = resolved(_MODEL_ENV_VARS.generation)
    embedding = resolved(_MODEL_ENV_VARS.embedding)
    ready = (
        server is not None
        and generation is not None
        and embedding is not None
        and generation.stat().st_size == _EXPECTED_TEST_MODEL_SIZES["generation"]
        and embedding.stat().st_size == _EXPECTED_TEST_MODEL_SIZES["embedding"]
    )
    if not ready:
        return SimpleNamespace(server=None, generation=None, embedding=None, ready=False)
    return SimpleNamespace(server=server, generation=generation, embedding=embedding, ready=True)


MODEL_ENV = _load_model_env()


@pytest.fixture(scope="session")
def model_env() -> SimpleNamespace:
    """The resolved real-model test environment (paths + readiness); see
    ``MODEL_ENV`` above. Integration modules gate themselves with
    ``pytest.mark.skipif(not MODEL_ENV.ready, ...)``."""
    return MODEL_ENV


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _require_loopback(address: object) -> None:
    """Raise unless ``address`` is a loopback endpoint (the S5-03 offline
    guard). Unix-socket paths are local by definition; anything else must
    be a tuple whose host is loopback."""
    if isinstance(address, str):
        return
    if isinstance(address, tuple) and address and isinstance(address[0], str):
        if address[0] in _LOOPBACK_HOSTS:
            return
    raise AssertionError(f"non-loopback connection attempted: {address!r}")


@pytest.fixture()
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """S5-03 offline evidence: while active, any connection attempt to a
    non-loopback address raises. The real-model integration module applies
    this to all of its tests via ``pytestmark``; a passing generation
    under the guard is the proof that the runtime needs no external
    traffic. (Primary evidence; the optional hard form on Linux is
    running the module under ``unshare -rn`` -- documented in
    ``docs/DEV_MODELS.md``.)"""
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        _require_loopback(address)
        return real_connect(sock, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        _require_loopback(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    yield


#: 32 chars -- comfortably above MIN_SESSION_TOKEN_LENGTH. Not a secret:
#: this value is fixed only so tests are deterministic and never resembles
#: a value that could be mistaken for a real, in-use credential.
VALID_TOKEN = "test-only-session-token-abcdef12"

SettingsFactory = Callable[..., Settings]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--service-exe",
        default=None,
        help=(
            "Run the supervised-process tests against this packaged service executable "
            "(S1-09; see docs/PACKAGING.md) instead of `python -m controlgauge_service`."
        ),
    )


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strips any ambient `CONTROLGAUGE_*` variables from the real shell so
    the suite's behavior never depends on what happens to be set on the
    machine running it -- every test builds `Settings` from explicit kwargs
    or its own `monkeypatch.setenv` calls instead."""
    import os

    for key in list(os.environ):
        if key.startswith("CONTROLGAUGE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Builds a `Settings` instance from explicit kwargs only.

    Deliberately does not fall back to the real process environment for
    any field under test, so tests are isolated from whatever
    `CONTROLGAUGE_*` variables happen to be set on the machine running
    them.
    """

    def _make(
        *,
        host: str = "127.0.0.1",
        port: int = 51823,
        environment: str = "production",
        session_token: str | None = VALID_TOKEN,
    ) -> Settings:
        return Settings(
            host=host,
            port=port,
            environment=environment,
            session_token=session_token,
        )

    return _make


@pytest.fixture
def make_app(make_settings: SettingsFactory) -> Callable[..., FastAPI]:
    def _make(**settings_kwargs: object) -> FastAPI:
        settings = make_settings(**settings_kwargs)
        return create_app(settings)

    return _make


@pytest.fixture
def make_client(make_app: Callable[..., FastAPI]) -> Callable[..., TestClient]:
    def _make(**settings_kwargs: object) -> TestClient:
        return TestClient(make_app(**settings_kwargs))

    return _make


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """The common case: production-mode app with a valid token configured."""
    return make_client()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


SeedDataDir = Callable[[Path], Path]


@pytest.fixture(scope="session")
def catalog_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database migrated to head with the shipped framework catalogs
    imported, built once per test session. Importing a full catalog takes
    about a second, so tests that only need *a* ready database start from a
    copy of this one; tests about migration and import themselves
    (`test_db_foundation.py`, `test_catalog_import.py`, the restart test in
    `test_supervised_process.py`) still start from an empty directory."""
    from controlgauge_service import storage
    from controlgauge_service.db import initialize_database

    directory = tmp_path_factory.mktemp("catalog-template")
    initialize_database(directory).dispose()
    return storage.database_path(directory)


@pytest.fixture
def seed_data_dir(catalog_template: Path) -> SeedDataDir:
    """Copies the template database into `data_dir` (created if needed)."""

    def _seed(data_dir: Path) -> Path:
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(catalog_template, data_dir / catalog_template.name)
        return data_dir

    return _seed


# --- S6-04: the loopback download stub -------------------------------------------

GGUF_HEAD_V3 = b"GGUF" + struct.pack("<I", 3)


def _gguf_body(seed: bytes, size: int = 4096) -> bytes:
    """A tiny file that passes the container sniff: GGUF v3 head + pad."""
    payload = GGUF_HEAD_V3 + seed
    return payload + b"\x00" * (size - len(payload))


@dataclass
class _StubState:
    """Per-server behavior the tests arrange before/while downloading."""

    files: dict[str, bytes] = field(default_factory=dict)
    #: Paths answered with this status instead of file content.
    status: dict[str, int] = field(default_factory=dict)
    #: Paths answered with a 302 to this location.
    redirect: dict[str, str] = field(default_factory=dict)
    ignore_range: bool = False
    #: Paths dropped mid-body (connection reset, no clean EOF).
    drop: set[str] = field(default_factory=set)
    #: Path -> seconds of sleep per 1 KiB chunk (a slow trickle).
    trickle: dict[str, float] = field(default_factory=dict)
    #: Mid-stream gate for deterministic cancel tests: for these paths,
    #: a Range-less (fresh) request serves the first GATE_PREFIX bytes,
    #: then BLOCKS until `gate` is set (bounded). A resumed request
    #: (Range present) is never gated.
    gate_paths: set[str] = field(default_factory=set)
    gate: threading.Event = field(default_factory=threading.Event)
    gate_prefix: int = 0
    range_headers_seen: list[str] = field(default_factory=list)
    request_count: dict[str, int] = field(default_factory=dict)


class _StubHandler(http.server.BaseHTTPRequestHandler):
    server_version = "Stub/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # never log request lines (they carry the stub's paths)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        state: _StubState = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        state.request_count[path] = state.request_count.get(path, 0) + 1
        if path in state.redirect:
            self.send_response(302)
            self.send_header("Location", state.redirect[path])
            self.end_headers()
            return
        if path in state.status:
            self.send_response(state.status[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content = state.files.get(path)
        if content is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        range_header = self.headers.get("Range")
        if range_header:
            state.range_headers_seen.append(range_header)
        start = 0
        if range_header and not state.ignore_range and range_header.startswith("bytes="):
            start = int(range_header[len("bytes=") :].split("-", 1)[0])
            body = content[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}")
        else:
            body = content
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if path in state.trickle:
            for offset in range(0, len(body), 1024):
                self.wfile.write(body[offset : offset + 1024])
                self.wfile.flush()
                time.sleep(state.trickle[path])
            return
        if path in state.gate_paths and range_header is None:
            # Serve the prefix, then block until the test opens the gate
            # (the download's first >=1 MiB chunk has been yielded and
            # written by then -- the cancel lands deterministically
            # mid-stream, not on a timing race).
            self.wfile.write(body[: state.gate_prefix])
            self.wfile.flush()
            state.gate.wait(timeout=5.0)
            self.wfile.write(body[state.gate_prefix :])
            self.wfile.flush()
            return
        if path in state.drop:
            half = len(body) // 2
            self.wfile.write(body[:half])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)


def _url(server: Any, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


class _StubServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the per-test `_StubState`."""

    state: _StubState


@pytest.fixture()
def stub_server() -> Any:
    """A loopback HTTP stub for download tests: KB-scale fixtures, no
    external network (loopback connections pass the offline guard). The
    tests arrange behavior through `server.state` (`_StubState`)."""
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    server.state = _StubState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5.0)
