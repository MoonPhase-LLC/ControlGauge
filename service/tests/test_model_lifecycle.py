"""S5-02: provider/manager lifecycle against fake children.

The provider's spawn point is wrapped (monkeypatching
``provider.spawn_child``) so the "llama-server" is a small
``sys.executable -c`` script: one that serves ``/health`` and chat
responses over the real loopback port from argv, one that sleeps
forever, one that exits immediately. Windows-safe by construction; no
real model is needed.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest

from controlgauge_service.model_runtime import childproc
from controlgauge_service.model_runtime import manager as manager_module
from controlgauge_service.model_runtime import provider as provider_module
from controlgauge_service.model_runtime.config import (
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.model_runtime.protocols import HealthStatus, ModelDescriptor
from controlgauge_service.model_runtime.provider import LlamaCppProvider

# --- fake children -----------------------------------------------------------

_SERVE_SCRIPT = """
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

argv = sys.argv
port = int(argv[argv.index("--port") + 1])

class Handler(BaseHTTPRequestHandler):
    def _respond(self, body):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._respond('{"status":"ok"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._respond(json.dumps({
            "choices": [{"message": {"content": json.dumps({"ok": True})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "timings": {"prompt_ms": 1.0, "predicted_ms": 1.0},
        }))

    def log_message(self, fmt, *args):
        pass

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""

_SLEEP_SCRIPT = "import time; time.sleep(120)"
_EXIT_SCRIPT = "import sys; sys.exit(3)"

_OK_SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


@pytest.fixture
def fake_child(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], list[childproc.ChildHandle]]]:
    """Install a fake child: real ``spawn_child`` of
    ``[sys.executable, -c, script, *llama_argv]``. Returns the list the
    spawned handles are recorded into; every leftover is terminated at
    teardown."""
    handles: list[childproc.ChildHandle] = []

    def _install(script: str) -> list[childproc.ChildHandle]:
        real_spawn = childproc.spawn_child

        def _spawn(argv: list[str], env: dict[str, str]) -> childproc.ChildHandle:
            handle = real_spawn([sys.executable, "-c", script, *argv], env)
            handles.append(handle)
            return handle

        monkeypatch.setattr(provider_module, "spawn_child", _spawn)
        return handles

    yield _install

    for handle in handles:
        handle.terminate(1.0)


def _config(tmp_path: Path, **kwargs: object) -> GenerationServerConfig:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")
    return GenerationServerConfig(model_path=model, model_id="test-model", **kwargs)  # type: ignore[arg-type]


def _provider(
    tmp_path: Path, runtime_binary: Path, **kwargs: object
) -> provider_module.LlamaCppProvider:
    return provider_module.LlamaCppProvider(
        _config(tmp_path, **kwargs), runtime_binary=runtime_binary, role="generation"
    )


def _binary(tmp_path: Path) -> Path:
    binary = tmp_path / "llama-server"
    binary.write_bytes(b"fake binary")
    return binary


# --- start() preconditions ---------------------------------------------------


def test_start_missing_binary_raises_runtime_binary_missing(tmp_path: Path) -> None:
    provider = _provider(tmp_path, tmp_path / "does-not-exist")

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.start()

    assert excinfo.value.code == "runtime_binary_missing"


def test_start_missing_model_file_raises_model_file_missing(tmp_path: Path) -> None:
    config = GenerationServerConfig(model_path=tmp_path / "missing.gguf", model_id="test-model")
    provider = provider_module.LlamaCppProvider(
        config, runtime_binary=_binary(tmp_path), role="generation"
    )

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.start()

    assert excinfo.value.code == "model_file_missing"


# --- start() against real (fake-script) children -----------------------------


def test_child_that_exits_immediately_raises_start_failed(
    tmp_path: Path,
    fake_child: Callable[[str], list[childproc.ChildHandle]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_child(_EXIT_SCRIPT)
    provider = _provider(tmp_path, _binary(tmp_path))

    with caplog.at_level(logging.WARNING, logger="controlgauge_service.model_runtime"):
        with pytest.raises(ModelRuntimeError) as excinfo:
            provider.start()

    assert excinfo.value.code == "start_failed"
    # No partial state: nothing is left installed after a failed start.
    assert provider._client is None
    assert provider._handle is None
    # The child's exit code is recorded structurally (diagnostic only).
    assert any(
        record.msg == "model_server_start_failed" and getattr(record, "exit_code", None) == 3
        for record in caplog.records
    )


def test_unresponsive_child_raises_start_timeout(
    tmp_path: Path, fake_child: Callable[[str], list[childproc.ChildHandle]]
) -> None:
    handles = fake_child(_SLEEP_SCRIPT)
    provider = _provider(tmp_path, _binary(tmp_path), start_timeout_s=2.0)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.start()

    assert excinfo.value.code == "start_timeout"
    # The deadline path kills the hung child before failing.
    assert len(handles) == 1
    assert handles[0].poll() is not None
    assert provider._client is None


def test_stop_terminates_child(
    tmp_path: Path, fake_child: Callable[[str], list[childproc.ChildHandle]]
) -> None:
    handles = fake_child(_SERVE_SCRIPT)
    provider = _provider(tmp_path, _binary(tmp_path))

    provider.start()
    assert provider.health_check().state == "ready"
    provider.stop()

    assert handles[0].poll() is not None  # exited within the stop grace
    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)
    assert excinfo.value.code == "not_loaded"


def test_stop_is_idempotent(
    tmp_path: Path, fake_child: Callable[[str], list[childproc.ChildHandle]]
) -> None:
    fake_child(_SERVE_SCRIPT)
    provider = _provider(tmp_path, _binary(tmp_path))

    provider.start()
    provider.stop()
    provider.stop()  # must be a no-op, not an error

    assert provider._client is None
    assert provider._handle is None


# --- closed-client error mapping ---------------------------------------------


def test_closed_client_maps_runtimeerror_to_transport_error(tmp_path: Path) -> None:
    """N2: httpx 0.28 raises a bare RuntimeError ("Cannot send a request,
    as the client has been closed.") for a closed client -- the post-stop
    in-flight call -- instead of a TransportError. It must map to the same
    fixed ``transport_error`` code, not escape error mapping as a 500."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")

    def _provider_with_closed_client(
        config: GenerationServerConfig | EmbeddingServerConfig, role: str
    ) -> provider_module.LlamaCppProvider:
        provider = provider_module.LlamaCppProvider(
            config,
            runtime_binary=_binary(tmp_path),
            role=role,  # type: ignore[arg-type]
        )
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        client.close()
        provider._client = client
        provider._stopped = False
        return provider

    generation = _provider_with_closed_client(
        GenerationServerConfig(model_path=model, model_id="test-model"), "generation"
    )
    with pytest.raises(ModelRuntimeError) as excinfo:
        generation.generate("p", _OK_SCHEMA, timeout_s=5.0)
    assert excinfo.value.code == "transport_error"

    embedding = _provider_with_closed_client(
        EmbeddingServerConfig(model_path=model, model_id="test-model"), "embedding"
    )
    with pytest.raises(ModelRuntimeError) as excinfo:
        embedding.embed("text")
    assert excinfo.value.code == "transport_error"


# --- manager switching semantics --------------------------------------------


class StubProvider:
    """Records its own lifecycle so switch ordering can be asserted."""

    events: list[tuple[str, int]] = []
    instances: list[StubProvider] = []
    counter = 0

    def __init__(
        self,
        config: GenerationServerConfig,
        runtime_binary: Path,
        *,
        role: str,
        on_dimension_learned: Callable[[int], None] | None = None,
    ) -> None:
        StubProvider.counter += 1
        self.ordinal = StubProvider.counter
        self.role = role
        self.started = False
        self.stopped = False
        self.start_error: str | None = None
        StubProvider.instances.append(self)
        StubProvider.events.append(("create", self.ordinal))

    def start(self) -> None:
        if self.start_error is not None:
            raise ModelRuntimeError(self.start_error)
        self.started = True
        StubProvider.events.append(("start", self.ordinal))

    def stop(self) -> None:
        self.stopped = True
        StubProvider.events.append(("stop", self.ordinal))

    def health_check(self) -> HealthStatus:
        return HealthStatus(state="ready")

    def describe(self) -> ModelDescriptor:
        return ModelDescriptor(
            model_id="test-model", display_name="test-model", role="generation", context_tokens=1
        )


@pytest.fixture
def stub_providers(monkeypatch: pytest.MonkeyPatch) -> type[StubProvider]:
    StubProvider.events = []
    StubProvider.instances = []
    StubProvider.counter = 0
    monkeypatch.setattr(manager_module, "LlamaCppProvider", StubProvider)
    return StubProvider


def _manager() -> ModelRuntimeManager:
    return ModelRuntimeManager(Path("llama-server"))


def test_manager_single_active_per_role(stub_providers: type[StubProvider]) -> None:
    manager = _manager()
    config = GenerationServerConfig(model_path=Path("m.gguf"), model_id="test-model")

    manager.start("generation", config, display_name="first")
    manager.start("generation", config, display_name="second")

    assert len(stub_providers.instances) == 2
    assert stub_providers.instances[0].stopped is True
    assert stub_providers.instances[1].started is True
    active = manager.get("generation")
    assert active is not None


def test_switch_stops_previous_provider_first(stub_providers: type[StubProvider]) -> None:
    manager = _manager()
    config = GenerationServerConfig(model_path=Path("m.gguf"), model_id="test-model")

    manager.start("generation", config, display_name="first")
    manager.start("generation", config, display_name="second")

    first, second = stub_providers.instances
    # The documented switch: stop the current provider BEFORE the
    # replacement starts; in-flight calls fail on their own closed client.
    assert first.stopped is True
    assert second.started is True
    events = stub_providers.events
    assert events.index(("stop", first.ordinal)) < events.index(("start", second.ordinal))
    active = manager.get("generation")
    assert active is not None
    assert active is cast(LlamaCppProvider, second)


def test_failed_switch_leaves_role_empty(stub_providers: type[StubProvider]) -> None:
    manager = _manager()
    config = GenerationServerConfig(model_path=Path("m.gguf"), model_id="test-model")

    manager.start("generation", config, display_name="first")
    # The next constructed provider fails to start.
    original_init = StubProvider.__init__

    def failing_init(self: StubProvider, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        self.start_error = "start_failed"

    StubProvider.__init__ = failing_init  # type: ignore[method-assign]
    try:
        with pytest.raises(ModelRuntimeError) as excinfo:
            manager.start("generation", config, display_name="second")
    finally:
        StubProvider.__init__ = original_init  # type: ignore[method-assign]

    assert excinfo.value.code == "start_failed"
    # Fail closed: the role is empty, never silently keeping the old model.
    assert manager.get("generation") is None
    assert manager.status("generation").state == "stopped"


def test_status_without_provider_is_stopped() -> None:
    manager = ModelRuntimeManager(None)

    assert manager.status("generation").state == "stopped"
    assert manager.status("embedding").state == "stopped"


def test_peek_status_is_structural_last_known(
    tmp_path: Path,
    fake_child: Callable[[str], list[childproc.ChildHandle]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: `peek_status` answers from recorded state WITHOUT contacting
    the child (the `GET /models` answer); `status` is the live check and
    records what the peek serves."""
    fake_child(_SERVE_SCRIPT)
    manager = ModelRuntimeManager(_binary(tmp_path))

    assert manager.peek_status("generation").state == "stopped"

    manager.start("generation", _config(tmp_path), display_name="test-model")
    peeked = manager.peek_status("generation")
    assert peeked.state == "ready"
    assert peeked.checked_at is None  # no capability check has run yet

    # The live check records the last-known state the peek then serves.
    assert manager.status("generation").state == "ready"
    assert manager.peek_status("generation").checked_at is not None

    # The peek never contacts the child.
    def _refuse(self: LlamaCppProvider) -> HealthStatus:
        raise AssertionError("peek_status must not contact the child")

    monkeypatch.setattr(LlamaCppProvider, "health_check", _refuse)
    assert manager.peek_status("generation").state == "ready"

    manager.stop("generation")
    assert manager.peek_status("generation").state == "stopped"


def test_start_without_runtime_binary_fails_closed(stub_providers: type[StubProvider]) -> None:
    manager = ModelRuntimeManager(None)
    config = GenerationServerConfig(model_path=Path("m.gguf"), model_id="test-model")

    with pytest.raises(ModelRuntimeError) as excinfo:
        manager.start("generation", config, display_name="x")

    assert excinfo.value.code == "runtime_binary_missing"
    assert manager.get("generation") is None


def test_runtime_version_extraction_is_bounded_build_metadata_only() -> None:
    """``describe()`` surfaces only the build string from /props -- never
    the model path or any other field the payload carries (T-10)."""
    # llama-server b12480 serves build_info as a flat string.
    assert (
        provider_module._extract_runtime_version({"build_info": "b12480-7f106b545"})
        == "b12480-7f106b545"
    )
    # Defensive: a dict-shaped build_info (other builds) still extracts.
    assert (
        provider_module._extract_runtime_version(
            {"build_info": {"build_number": 12480, "commit": "7f106b545"}}
        )
        == "b12480 7f106b545"
    )
    # Bounded: an absurdly long build string is truncated, never echoed.
    assert provider_module._extract_runtime_version({"build_info": "x" * 500}) == "x" * 64
    # Closed: absent/unrelated fields yield None -- the model path that
    # /props also carries is never read here.
    assert provider_module._extract_runtime_version({}) is None
    assert provider_module._extract_runtime_version({"model_path": "/models/m.gguf"}) is None
