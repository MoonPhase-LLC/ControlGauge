"""S5-02: the fixed error taxonomy, exercised through httpx.MockTransport
(no sockets, Windows CI-safe). Every transport/status/child condition
maps to exactly one ``ModelRuntimeError.code``; embedding inputs are
never silently truncated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from controlgauge_service.model_runtime.config import EmbeddingServerConfig, GenerationServerConfig
from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.provider import LlamaCppProvider

_OK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}


def _provider(
    role: str = "generation",
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    **config_kwargs: Any,
) -> LlamaCppProvider:
    config: GenerationServerConfig | EmbeddingServerConfig
    if role == "generation":
        config = GenerationServerConfig(
            model_path=Path("model.gguf"), model_id="test", **config_kwargs
        )
    else:
        config = EmbeddingServerConfig(
            model_path=Path("model.gguf"), model_id="test", **config_kwargs
        )
    provider = LlamaCppProvider(config, runtime_binary=Path("llama-server"), role=role)  # type: ignore[arg-type]
    if handler is not None:
        provider._client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:51997",
            trust_env=False,
        )
    return provider


def _chat_body(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def test_generate_maps_401_to_auth_rejected() -> None:
    provider = _provider(handler=lambda request: httpx.Response(401, json={}))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "auth_rejected"


def test_generate_maps_timeout_to_request_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler=handler)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "request_timeout"


def test_generate_maps_connect_error_to_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler=handler)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "transport_error"


def test_generate_maps_400_to_invalid_request() -> None:
    provider = _provider(handler=lambda request: httpx.Response(400, json={"error": {}}))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "invalid_request"


def test_generate_maps_5xx_to_server_error() -> None:
    provider = _provider(handler=lambda request: httpx.Response(500, json={}))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "server_error"


def test_generate_maps_other_client_errors_to_invalid_request() -> None:
    provider = _provider(handler=lambda request: httpx.Response(404, json={}))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "invalid_request"


def test_child_exit_maps_to_child_exited() -> None:
    """A child that has exited is detected before any request is made."""

    class ExitedHandle:
        def poll(self) -> int | None:
            return 3  # exited with code 3

    provider = _provider(handler=lambda request: httpx.Response(200, json=_chat_body("{}")))
    provider._handle = ExitedHandle()  # type: ignore[assignment]

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "child_exited"


def test_stopped_provider_maps_to_not_loaded() -> None:
    provider = _provider(handler=lambda request: httpx.Response(200, json=_chat_body("{}")))
    provider.stop()

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "not_loaded"


def test_embed_rejects_overlong_input_without_truncation() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    provider = _provider(role="embedding", handler=handler, max_input_chars=10)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.embed("x" * 11)

    assert excinfo.value.code == "input_too_long"
    assert calls == []  # refused before any request


def test_embed_rejects_non_finite() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.dumps({"data": [{"embedding": [0.5, float("nan")]}]})
        return httpx.Response(
            200,
            content=payload.encode(),
            headers={"content-type": "application/json"},
        )

    provider = _provider(role="embedding", handler=handler)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.embed("hello")

    assert excinfo.value.code == "non_finite_embedding"


def test_embed_rejects_dimension_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]})

    provider = _provider(role="embedding", handler=handler, expected_dimensions=3)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.embed("hello")

    assert excinfo.value.code == "embedding_dimension_mismatch"


def test_embed_maps_transport_failure_to_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _provider(role="embedding", handler=handler)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.embed("hello")

    assert excinfo.value.code == "transport_error"


def test_generate_invalid_json_is_invalid_output() -> None:
    provider = _provider(
        handler=lambda request: httpx.Response(200, json=_chat_body("not json at all"))
    )

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "invalid_output"


def test_generate_non_object_json_is_invalid_output() -> None:
    provider = _provider(handler=lambda request: httpx.Response(200, json=_chat_body("[1, 2]")))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "invalid_output"


def test_generate_missing_choices_is_invalid_output() -> None:
    provider = _provider(handler=lambda request: httpx.Response(200, json={"nope": True}))

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "invalid_output"


def test_generate_schema_violation_is_schema_validation_failed() -> None:
    provider = _provider(
        handler=lambda request: httpx.Response(200, json=_chat_body('{"ok": "yes"}'))
    )

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert excinfo.value.code == "schema_validation_failed"


def test_error_message_is_fixed_per_code() -> None:
    """str(exc) never carries dynamic content -- only the fixed message."""
    error = ModelRuntimeError("auth_rejected")

    assert str(error) == "The model server rejected the credential."
    assert error.code == "auth_rejected"


def test_unknown_code_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        ModelRuntimeError("made_up_code")
