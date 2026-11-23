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
from controlgauge_service.model_runtime.errors import (
    _MESSAGES,
    CATALOG_ERROR_CODES,
    DOWNLOAD_ERROR_CODES,
    MODEL_RUNTIME_ERROR_CODES,
    SPRINT6_ERROR_CODES,
    ModelRuntimeError,
)
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
    on_dimension_learned: Callable[[int], None] | None = None,
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
    provider = LlamaCppProvider(
        config,
        runtime_binary=Path("llama-server"),
        role=role,  # type: ignore[arg-type]
        on_dimension_learned=on_dimension_learned,
    )
    if handler is not None:
        provider._client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:51997",
            trust_env=False,
            # Mirror _make_client: the credential rides client-level
            # headers on every request (unit-level half of that contract;
            # the real-server half is the 401 matrix in the integration
            # module).
            headers={"Authorization": "Bearer unit-test-credential"},
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


def test_embed_dimension_learned_once() -> None:
    """Learn-and-pin (S5-04): the first successful embed reports the
    observed dimension via the callback exactly once; a later answer with
    a different dimensionality is refused (``embedding_dimension_mismatch``)
    and surfaces as an unhealthy capability check."""
    learned: list[int] = []

    def seven68_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.5] * 768}]})

    provider = _provider(
        role="embedding",
        handler=seven68_handler,
        on_dimension_learned=learned.append,
    )

    provider.embed("first")
    provider.embed("second")
    assert learned == [768]  # learned once, not per call

    def four_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]})

    provider._client = httpx.Client(
        transport=httpx.MockTransport(four_handler),
        base_url="http://127.0.0.1:51997",
        trust_env=False,
        headers={"Authorization": "Bearer unit-test-credential"},
    )

    status = provider.health_check()

    assert status.state == "unhealthy"
    assert status.detail_code == "embedding_dimension_mismatch"


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


# --- S5-03: the generate() request contract ---------------------------------


def test_generate_request_carries_schema_and_budget() -> None:
    """The wire contract: the caller's schema rides ``response_format``
    (sampler enforcement), the configured budget caps the answer, the
    chat template is applied (messages, not a raw completion), and the
    Authorization header rides every request (readiness included)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_chat_body('{"ok": true}'))

    provider = _provider(handler=handler, max_output_tokens=77)

    provider.generate("p", _OK_SCHEMA, timeout_s=5.0, system_prompt="sys")

    request = calls[0]
    body = json.loads(request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == _OK_SCHEMA
    assert body["max_tokens"] == 77
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "p"},
    ]
    assert request.headers["Authorization"].startswith("Bearer ")


def test_generate_respects_timeout_parameter() -> None:
    """The caller's ``timeout_s`` rides the per-request timeout; a server
    slower than it maps to ``request_timeout`` (never a hung call)."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        raise httpx.ReadTimeout("slow child", request=request)

    provider = _provider(handler=handler)

    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate("p", _OK_SCHEMA, timeout_s=1.25)

    assert excinfo.value.code == "request_timeout"
    assert seen["timeout"] == {
        "connect": 1.25,
        "read": 1.25,
        "write": 1.25,
        "pool": 1.25,
    }


def test_generate_never_persists() -> None:
    """By construction: ``generate()`` returns parsed, validated data and
    nothing else -- the provider carries no database, session, or store
    collaborator that could persist anything (Sprint 5 writes no
    analysis tables). The attribute set is pinned so any future
    persistence collaborator would have to appear here first."""
    provider = _provider(
        handler=lambda request: httpx.Response(200, json=_chat_body('{"ok": true}'))
    )

    result = provider.generate("p", _OK_SCHEMA, timeout_s=5.0)

    assert result.data == {"ok": True}
    assert set(provider.__dict__) == {
        "_config",
        "_runtime_binary",
        "_role",
        "_on_dimension_learned",
        "_credential",
        "_handle",
        "_client",
        "_port",
        "_stopped",
        "_dimension_learned",
        "_observed_dimensions",
    }


# --- S6-04: the Sprint 6 download/catalog codes ---------------------------------


@pytest.mark.parametrize("code", SPRINT6_ERROR_CODES)
def test_sprint6_code_has_exactly_one_fixed_message(code: str) -> None:
    """Every Sprint 6 code constructs, carries its one fixed message, and
    that message never contains a path, URL, or filename (T-10: the fixed
    text is all a client ever learns)."""
    error = ModelRuntimeError(code)
    assert error.code == code
    assert str(error) == _MESSAGES[code]
    assert "://" not in str(error)
    assert "/" not in str(error)
    assert "\\" not in str(error)
    assert ".gguf" not in str(error)


def test_sprint6_codes_are_in_the_runtime_taxonomy() -> None:
    """The S6 tuples are the taxonomy's members, not a side list: every
    Sprint 6 code is a valid `ModelRuntimeError` code and every download
    code is reachable from the routes' status maps."""
    assert set(SPRINT6_ERROR_CODES) <= set(MODEL_RUNTIME_ERROR_CODES)
    assert "download_cancelled" not in MODEL_RUNTIME_ERROR_CODES  # a state, not an error
    assert set(DOWNLOAD_ERROR_CODES) < set(MODEL_RUNTIME_ERROR_CODES)
    assert set(CATALOG_ERROR_CODES) < set(MODEL_RUNTIME_ERROR_CODES)


def test_registry_codes_are_pinned_and_routable() -> None:
    """The S6-05 registry codes exist, are members of the Sprint 6 union
    and the runtime taxonomy, and every one is reachable from the
    endpoints router's fixed status map (`model_endpoints.py`)."""
    from controlgauge_service.model_runtime.errors import REGISTRY_ERROR_CODES
    from controlgauge_service.routes.model_endpoints import _REGISTRY_ERROR_STATUS

    assert set(REGISTRY_ERROR_CODES) <= set(MODEL_RUNTIME_ERROR_CODES)
    assert set(REGISTRY_ERROR_CODES) <= set(SPRINT6_ERROR_CODES)
    assert set(_REGISTRY_ERROR_STATUS) == set(REGISTRY_ERROR_CODES)
    # The fixed statuses: request problems 422, conflicts 409, unknown
    # id 404, an unverifiable endpoint 502.
    assert _REGISTRY_ERROR_STATUS["registry_endpoint_invalid"] == 422
    assert _REGISTRY_ERROR_STATUS["registry_endpoint_not_loopback"] == 422
    assert _REGISTRY_ERROR_STATUS["registry_endpoint_unreachable"] == 502
    assert _REGISTRY_ERROR_STATUS["registry_duplicate"] == 409
    assert _REGISTRY_ERROR_STATUS["endpoint_not_found"] == 404
    assert _REGISTRY_ERROR_STATUS["endpoint_attached"] == 409
