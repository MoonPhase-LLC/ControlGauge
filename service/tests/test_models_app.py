"""S5-02: app/entry-point wiring for the model runtime manager.

The 503-without-manager contract rides the shared `get_model_runtime`
dependency (real model routes arrive in S5-05; the dependency and its
fail-closed code are the S5-02 deliverable), and both process entry
points must stop the manager beside `ingestion.stop()` at shutdown.
"""

from __future__ import annotations

import contextlib
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn

from controlgauge_service import protocol as protocol_module
from controlgauge_service import supervised as supervised_module
from controlgauge_service.app import create_app
from controlgauge_service.config import Settings
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.routes.deps import get_model_runtime
from controlgauge_service.routes.errors import ApiError

#: Resolved through ``sys.modules``: the package re-exports a ``main``
#: *function* under TYPE_CHECKING, so ``from controlgauge_service import
#: main`` is statically the function while runtime needs the module.
main_module = importlib.import_module("controlgauge_service.main")


def test_model_runtime_stored_on_app_state() -> None:
    manager = ModelRuntimeManager(None)

    app = create_app(Settings(), model_runtime=manager)

    assert app.state.model_runtime is manager


def test_model_runtime_defaults_to_none_on_app_state() -> None:
    app = create_app(Settings())

    assert app.state.model_runtime is None


def test_models_dependency_503_without_manager() -> None:
    """`model_runtime_unavailable` mirrors the ingestion/database 503
    pattern: absent capability, fixed code, no detail."""
    app = create_app(Settings())

    with pytest.raises(ApiError) as excinfo:
        get_model_runtime(SimpleNamespace(app=app))  # type: ignore[arg-type]

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "model_runtime_unavailable"


def test_models_dependency_returns_manager_when_wired() -> None:
    manager = ModelRuntimeManager(None)
    app = create_app(Settings(), model_runtime=manager)

    assert get_model_runtime(SimpleNamespace(app=app)) is manager  # type: ignore[arg-type]


class _RecordingManager(ModelRuntimeManager):
    """Wiring probe: records `stop_all` without touching real children."""

    def __init__(self, runtime_binary: Path | None) -> None:
        super().__init__(runtime_binary)
        self.stop_all_calls = 0

    def stop_all(self) -> None:  # noqa: D102 - probe
        self.stop_all_calls += 1


class _DummyDatabase:
    def dispose(self) -> None:
        return None


def _patch_common(monkeypatch: pytest.MonkeyPatch, manager: _RecordingManager) -> None:
    monkeypatch.setattr(main_module, "initialize_database", lambda: _DummyDatabase())
    monkeypatch.setattr(main_module, "start_ingestion", lambda database: None)
    monkeypatch.setattr(main_module, "ModelRuntimeManager", lambda settings: manager)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)


def test_stop_all_called_on_shutdown_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main.py` (standalone) must stop the model runtime beside
    `ingestion.stop()` after `uvicorn.run` returns."""
    manager = _RecordingManager(None)
    _patch_common(monkeypatch, manager)

    main_module.main()

    assert manager.stop_all_calls == 1


def test_stop_all_called_on_shutdown_supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    """`supervised.py` must stop the model runtime in the same `finally`
    as `ingestion.stop()` -- even though the supervised flow ends with a
    non-clean protocol outcome here (SystemExit 1)."""
    manager = _RecordingManager(None)
    monkeypatch.setattr(supervised_module, "initialize_database", lambda: _DummyDatabase())
    monkeypatch.setattr(supervised_module, "start_ingestion", lambda database: None)
    monkeypatch.setattr(supervised_module, "ModelRuntimeManager", lambda settings: manager)
    monkeypatch.setattr(protocol_module, "read_startup_secret", lambda stdin: b"x" * 32)
    monkeypatch.setattr(protocol_module, "write_endpoint_ready", lambda *a, **k: None)

    def _protocol_error(*args: Any, **kwargs: Any) -> None:
        raise protocol_module.ProtocolError("test-end")

    monkeypatch.setattr(protocol_module, "read_install_token_or_shutdown", _protocol_error)

    async def _fake_serve(self: uvicorn.Server, sockets: Any = None) -> None:
        return None

    monkeypatch.setattr(uvicorn.Server, "serve", _fake_serve)

    with contextlib.suppress(SystemExit):
        supervised_module.main()

    assert manager.stop_all_calls == 1
