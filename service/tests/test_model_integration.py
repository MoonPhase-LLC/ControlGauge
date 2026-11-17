"""S5-02 (b): real-model integration against the dev llama-server and
the two PD-023 dev models. Skips cleanly everywhere the dev environment
is not configured (Windows CI never sets it); collected everywhere.

Evidence produced here feeds ``docs/DEV_MODELS.md`` (auth matrix,
split-GGUF verification, credential/argv contract, hung-child
detection).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from controlgauge_service.config import is_valid_session_token_format
from controlgauge_service.model_runtime.args import build_server_args
from controlgauge_service.model_runtime.config import (
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.provider import LlamaCppProvider
from tests.conftest import MODEL_ENV

pytestmark = pytest.mark.skipif(
    not MODEL_ENV.ready,
    reason="real llama-server binary and/or dev models not configured",
)

_OK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}
_HEALTH_PROMPT = 'Reply with the JSON object {"ok": true}.'


def _generation_provider(**config_kwargs: object) -> LlamaCppProvider:
    config = GenerationServerConfig(
        model_path=MODEL_ENV.generation,
        model_id="dev-generation",
        **config_kwargs,  # type: ignore[arg-type]
    )
    return LlamaCppProvider(
        config,
        runtime_binary=MODEL_ENV.server,
        role="generation",
    )


def _embedding_provider(**config_kwargs: object) -> LlamaCppProvider:
    config = EmbeddingServerConfig(
        model_path=MODEL_ENV.embedding,
        model_id="dev-embedding",
        **config_kwargs,  # type: ignore[arg-type]
    )
    return LlamaCppProvider(
        config,
        runtime_binary=MODEL_ENV.server,
        role="embedding",
    )


@pytest.fixture(scope="session")
def generation_provider() -> Iterator[LlamaCppProvider]:
    """One generation model load shared by the tests that only READ from
    the running server (auth matrix, argv contract)."""
    provider = _generation_provider()
    provider.start()
    try:
        yield provider
    finally:
        provider.stop()


@pytest.fixture(scope="session")
def embedding_provider() -> Iterator[LlamaCppProvider]:
    provider = _embedding_provider()
    provider.start()
    try:
        yield provider
    finally:
        provider.stop()


def test_integration_start_health_stop_generation_model() -> None:
    """Split GGUF loads from shard 1 (the split-loading assumption, risk
    register #3); the capability-specific health check returns ``ready``
    with a well-formed bounded generation; stop is clean."""
    provider = _generation_provider()
    try:
        provider.start()
        status = provider.health_check()
        assert status.state == "ready"
        assert status.detail_code is None
        generation = provider.generate(_HEALTH_PROMPT, _OK_SCHEMA, timeout_s=120.0)
        assert generation.data == {"ok": True}
        descriptor = provider.describe()
        assert descriptor.model_id == "dev-generation"
        assert descriptor.role == "generation"
        assert descriptor.runtime_version is None or isinstance(descriptor.runtime_version, str)
    finally:
        provider.stop()
    with pytest.raises(ModelRuntimeError) as excinfo:
        provider.generate(_HEALTH_PROMPT, _OK_SCHEMA, timeout_s=5.0)
    assert excinfo.value.code == "not_loaded"


def test_integration_unauthorized_local_request_rejected(
    generation_provider: LlamaCppProvider, embedding_provider: LlamaCppProvider
) -> None:
    """T-23 evidence: on each child's port, WITHOUT the credential every
    endpoint rejects with 401; WITH that child's own per-launch
    credential (D-017) the supported endpoint serves 200; and the OTHER
    launch's credential is rejected -- one launch's credential never
    opens another child. The provider's own requests always carry the
    credential, so its readiness/health succeeded before this test ran."""
    credential = generation_provider._credential
    embedding_credential = embedding_provider._credential
    generation_port = generation_provider._port
    embedding_port = embedding_provider._port
    assert credential and embedding_credential and generation_port and embedding_port
    assert credential != embedding_credential, "credentials must be per-launch"

    chat_body = {
        "messages": [{"role": "user", "content": _HEALTH_PROMPT}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": _OK_SCHEMA},
        },
        "max_tokens": 32,
        "stream": False,
    }

    for port, path, body in (
        (generation_port, "/v1/chat/completions", chat_body),
        (embedding_port, "/v1/embeddings", {"input": "controlgauge"}),
    ):
        anonymous = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=60.0)
        try:
            response = anonymous.post(path, json=body)
        finally:
            anonymous.close()
        assert response.status_code == 401, f"{path} served without the credential"

    authenticated = httpx.Client(
        base_url=f"http://127.0.0.1:{generation_port}",
        trust_env=False,
        timeout=180.0,
        headers={"Authorization": f"Bearer {credential}"},
    )
    try:
        response = authenticated.post("/v1/chat/completions", json=chat_body)
    finally:
        authenticated.close()
    assert response.status_code == 200

    authed_embed = httpx.Client(
        base_url=f"http://127.0.0.1:{embedding_port}",
        trust_env=False,
        timeout=60.0,
        headers={"Authorization": f"Bearer {embedding_credential}"},
    )
    try:
        response = authed_embed.post("/v1/embeddings", json={"input": "controlgauge"})
    finally:
        authed_embed.close()
    assert response.status_code == 200

    cross = httpx.Client(
        base_url=f"http://127.0.0.1:{embedding_port}",
        trust_env=False,
        timeout=60.0,
        headers={"Authorization": f"Bearer {credential}"},
    )
    try:
        response = cross.post("/v1/embeddings", json={"input": "controlgauge"})
    finally:
        cross.close()
    assert response.status_code == 401, "another launch's credential opened this child"


def test_integration_credential_not_in_child_cmdline(
    generation_provider: LlamaCppProvider,
) -> None:
    """D-017: the credential travels via the child environment, never
    argv. Linux reads the real ``/proc/<pid>/cmdline``; other platforms
    assert the argv contract through ``build_server_args``."""
    credential = generation_provider._credential
    handle = generation_provider._handle
    assert credential and handle is not None

    if sys.platform == "linux":
        cmdline_path = f"/proc/{handle.pid}/cmdline"
        with open(cmdline_path, "rb") as fh:
            argv = fh.read().split(b"\0")
        assert argv, "cmdline unreadable"
        joined = b" ".join(argv)
        assert credential.encode() not in joined
        assert b"--api-key" not in joined
        assert b"LLAMA_API_KEY" not in joined
    else:
        args = build_server_args(generation_provider._config, 51000)
        assert credential not in " ".join(args)


@pytest.mark.skipif(sys.platform != "linux", reason="SIGSTOP is POSIX-only")
def test_integration_hung_child_detected() -> None:
    """D-017: a hung subprocess that accepts connections but never
    answers must be detectable -- health_check returns unhealthy with
    ``request_timeout``, never ready (bounded real call)."""
    provider = _generation_provider(health_timeout_s=3.0)
    try:
        provider.start()
        assert provider.health_check().state == "ready"
        handle = provider._handle
        assert handle is not None
        sigstop = getattr(signal, "SIGSTOP", None)
        sigcont = getattr(signal, "SIGCONT", None)
        assert sigstop is not None and sigcont is not None  # linux-guarded above
        os.kill(handle.pid, sigstop)
        try:
            started = time.monotonic()
            status = provider.health_check()
            elapsed = time.monotonic() - started
        finally:
            os.kill(handle.pid, sigcont)
        assert status.state == "unhealthy"
        assert status.detail_code == "request_timeout"
        assert elapsed < 30.0  # bounded by health_timeout_s, not hung forever
    finally:
        provider.stop()


def test_integration_credential_format_is_header_safe(
    generation_provider: LlamaCppProvider,
) -> None:
    """The per-launch credential is 43-char unpadded base64url (the same
    alphabet the D-009 session token uses)."""
    assert is_valid_session_token_format(generation_provider._credential)


def test_integration_props_metadata_is_closed(
    generation_provider: LlamaCppProvider,
) -> None:
    """``describe()`` extracts only build/version metadata from the
    authenticated /props payload -- the model path it also carries is
    never copied into the descriptor."""
    descriptor = generation_provider.describe()
    raw = json.dumps(descriptor.__dict__)
    assert str(MODEL_ENV.generation) not in raw
    assert str(MODEL_ENV.server) not in raw
    # The pinned dev binary exposes build_info ("b12480-7f106b545");
    # describe() must surface exactly that build metadata.
    assert descriptor.runtime_version is not None
    assert descriptor.runtime_version.startswith("b12480")
