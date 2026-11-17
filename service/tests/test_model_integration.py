"""S5-02 (b) + S5-03 (b): real-model integration against the dev
llama-server and the two PD-023 dev models. Skips cleanly everywhere the
dev environment is not configured (Windows CI never sets it); collected
everywhere. Every test runs under the ``offline_guard`` fixture
(module-level ``pytestmark``): any non-loopback connection attempt fails
the test, so a passing run is itself the offline evidence.

Evidence produced here feeds ``docs/DEV_MODELS.md`` (auth matrix,
split-GGUF verification, credential/argv contract, hung-child detection,
response_format nesting, sampler-enforcement vs semantic quality).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import signal
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from controlgauge_service.app import create_app
from controlgauge_service.config import Settings, is_valid_session_token_format
from controlgauge_service.db import initialize_database
from controlgauge_service.model_runtime.args import build_server_args
from controlgauge_service.model_runtime.config import (
    MIN_MODEL_FILE_BYTES,
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from controlgauge_service.model_runtime.errors import ModelRuntimeError
from controlgauge_service.model_runtime.manager import ModelRuntimeManager
from controlgauge_service.model_runtime.provider import LlamaCppProvider
from controlgauge_service.model_runtime.schemas import (
    ENUM_SCHEMA,
    ENUM_SHAPE_SCHEMA,
    NESTED_SCHEMA,
    SIMPLE_FLAT_SCHEMA,
)
from controlgauge_service.routes.models import IMPORT_PROVENANCE_WARNING
from tests.conftest import EXPECTED_EMBEDDING_DIMENSIONS, MODEL_ENV, VALID_TOKEN

pytestmark = [
    pytest.mark.skipif(
        not MODEL_ENV.ready,
        reason="real llama-server binary and/or dev models not configured",
    ),
    # S5-03 offline evidence: every integration test runs with the guard
    # that fails any non-loopback connection attempt.
    pytest.mark.usefixtures("offline_guard"),
]

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


# --- S5-03: structured generation against the real model ---------------------


_FLAT_PROMPT = (
    'Produce the JSON object described by the schema. control_id: "AC-2"; ' "confidence: 0.9."
)


def test_integration_structured_generation_flat_schema_roundtrip(
    generation_provider: LlamaCppProvider,
) -> None:
    """Dev-verify step (S5-03): b12480 accepts the OpenAI-style
    ``response_format.json_schema`` nesting on the chat endpoint (the
    ``/completion`` fallback is NOT needed), the answer parses,
    validates deterministically, and echoes the asked-for values."""
    generation = generation_provider.generate(
        _FLAT_PROMPT,
        SIMPLE_FLAT_SCHEMA,
        timeout_s=120.0,
    )

    assert generation.data["control_id"] == "AC-2"
    assert 0.0 <= generation.data["confidence"] <= 1.0


def test_integration_structured_generation_nested_schema_roundtrip(
    generation_provider: LlamaCppProvider,
) -> None:
    """Nested object + array-of-objects shape round-trips: the grammar
    forces the structure, the model fills the asked-for values."""
    generation = generation_provider.generate(
        'Produce the JSON object described by the schema for control "AC-6", '
        "with exactly these two observation notes: "
        '"privilege review current" and "exceptions logged".',
        NESTED_SCHEMA,
        timeout_s=120.0,
    )

    assessment = generation.data["assessment"]
    assert assessment["control_id"] == "AC-6"
    assert {o["note"] for o in assessment["observations"]} == {
        "privilege review current",
        "exceptions logged",
    }


def test_integration_structured_generation_enum_schema_roundtrip(
    generation_provider: LlamaCppProvider,
) -> None:
    """The V0.1 relationship_type enum shape round-trips: the emitted
    member is the asked-for one and the confidence is in range."""
    generation = generation_provider.generate(
        "Produce the JSON object described by the schema. The relationship "
        "of the evidence to the control is SUPPORTS; confidence 0.8.",
        ENUM_SCHEMA,
        timeout_s=120.0,
    )

    assert generation.data["relationship_type"] == "SUPPORTS"
    assert 0.0 <= generation.data["confidence"] <= 1.0


def test_integration_structured_generation_respects_max_tokens(
    generation_provider: LlamaCppProvider,
) -> None:
    """A one-token budget truncates the grammar-constrained answer; the
    truncated text is surfaced as ``invalid_output`` -- never silently
    accepted as a usable result (bounded calls, S5-03 risk register)."""
    provider = _generation_provider(max_output_tokens=1)
    provider.start()
    try:
        with pytest.raises(ModelRuntimeError) as excinfo:
            provider.generate(
                "Produce the assessment JSON object described by the schema.",
                NESTED_SCHEMA,
                timeout_s=60.0,
            )
    finally:
        provider.stop()

    assert excinfo.value.code == "invalid_output"


def test_integration_sampler_enforcement_vs_semantic_quality(
    generation_provider: LlamaCppProvider,
) -> None:
    """PD-023 evidence, N=5: with the grammar enforcing the enum shape,
    EVERY answer is schema-conformant -- even for a nonsensical prompt,
    where the content is conformant but meaningless. Assertions cover
    conformance only; the semantic observation is documentation
    (DEV_MODELS.md), not a gate.

    The schema here is ``ENUM_SHAPE_SCHEMA`` (enum + number shape): on
    b12480 the grammar does NOT enforce numeric minimum/maximum -- a
    free-invented ``0.95…E1000000000000000`` (parses to ``inf``) passes
    the grammar and is caught downstream by deterministic validation
    (``schema_validation_failed``). Claiming "always conformant" for the
    range would assert something the sampler cannot guarantee; see
    DEV_MODELS.md."""
    prompt = (
        "Produce the JSON object described by the schema describing how the "
        "number seven migrates for the winter, in iambic pentameter."
    )
    outputs: list[dict[str, Any]] = []
    for _ in range(5):
        generation = generation_provider.generate(prompt, ENUM_SHAPE_SCHEMA, timeout_s=120.0)
        outputs.append(generation.data)

    for data in outputs:
        assert data["relationship_type"] in {
            "SUPPORTS",
            "PARTIALLY_SUPPORTS",
            "REFERENCES",
            "CONFLICTS_WITH",
        }
        # Shape-only conformance: the grammar guarantees a number, not its
        # range (free-invented overflowing exponents are observed and are
        # deterministic validation's job to refuse -- DEV_MODELS.md §5.1).
        assert isinstance(data["confidence"], int | float)

    # The evidence itself (conformant shape, arbitrary content) rides the
    # test log for the DEV_MODELS.md record.
    logging.getLogger("tests.model_integration").info(
        "sampler-enforcement evidence (N=5, nonsensical prompt): %s",
        json.dumps(outputs),
    )


def test_integration_offline_no_non_loopback_sockets(
    generation_provider: LlamaCppProvider,
) -> None:
    """With the module-wide offline guard active, a real structured
    generation still succeeds: the runtime's only network path is its
    loopback child. (Primary evidence; the hard form on Linux is
    running this module under ``unshare -rn``, documented in
    DEV_MODELS.md.)"""
    generation = generation_provider.generate(
        _FLAT_PROMPT,
        SIMPLE_FLAT_SCHEMA,
        timeout_s=120.0,
    )

    assert generation.data["control_id"] == "AC-2"


# --- S5-04: embeddings against the real model --------------------------------


def test_integration_embedding_finite_and_dimensioned(
    embedding_provider: LlamaCppProvider,
) -> None:
    """Real nomic embeddings: every value finite, and the dimensionality
    equals the conftest-pinned expectation (768) as reported by the
    server -- the learn-and-pin path reports the same number through
    ``describe()``. On mismatch this test fails and DEV_MODELS.md is
    corrected (verify, don't assert blindly)."""
    vector = embedding_provider.embed("controlgauge health check")

    assert len(vector) == EXPECTED_EMBEDDING_DIMENSIONS
    assert all(math.isfinite(value) for value in vector)

    descriptor = embedding_provider.describe()
    assert descriptor.embedding_dimensions == EXPECTED_EMBEDDING_DIMENSIONS


def test_integration_embedding_deterministic(
    embedding_provider: LlamaCppProvider,
) -> None:
    """Same-process determinism: two embeds of identical input return
    exactly identical vectors (restart determinism is a baseline
    observation, not a guarantee)."""
    first = embedding_provider.embed("determinism probe for sprint 5")
    second = embedding_provider.embed("determinism probe for sprint 5")

    assert first == second


def test_integration_embedding_input_limit_enforced(
    embedding_provider: LlamaCppProvider,
) -> None:
    """No silent truncation: input over ``max_input_chars`` is refused
    with ``input_too_long``, never embedded short. (The char bound is
    Sprint 5's enforced proxy; tokenizer-exact limits are Sprint 6's
    catalog concern per D-023.)"""
    with pytest.raises(ModelRuntimeError) as excinfo:
        embedding_provider.embed("x" * 33_000)  # > EMBEDDING_DEFAULT_MAX_INPUT_CHARS

    assert excinfo.value.code == "input_too_long"


def test_integration_embedding_health_check_capability_specific(
    embedding_provider: LlamaCppProvider,
) -> None:
    """D-023: the embedding capability's health check is a real embed,
    never a generation call -- proven from the server side: a generation
    request to the embedding-mode server is refused (4xx, and not the
    401 that would mean an auth problem), while the embedding health
    check returns ``ready``."""
    credential = embedding_provider._credential
    port = embedding_provider._port
    assert credential and port

    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        trust_env=False,
        timeout=60.0,
        headers={"Authorization": f"Bearer {credential}"},
    )
    try:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "max_tokens": 8},
        )
    finally:
        client.close()

    assert 400 <= response.status_code < 600
    assert response.status_code != 401, "auth failure would make this evidence inconclusive"

    assert embedding_provider.health_check().state == "ready"


# --- S5-05: the import API against the real models -----------------------------


class _ModelsApi:
    """Module-scope harness for the S5-05 route tests: a real app (real
    database + real per-capability manager + the real dev binary) with
    both dev models imported through the import route exactly once --
    the multi-GB generation shard is streamed and hashed a single time
    per module. The two import responses are kept for the contract
    assertions in the lifecycle test."""

    def __init__(
        self,
        client: TestClient,
        manager: ModelRuntimeManager,
        headers: dict[str, str],
        generation_import: dict[str, Any],
        embedding_import: dict[str, Any],
    ) -> None:
        self.client = client
        self.manager = manager
        self.headers = headers
        self.generation_import = generation_import
        self.embedding_import = embedding_import

    @property
    def generation_id(self) -> str:
        identifier: str = self.generation_import["model"]["id"]
        return identifier

    @property
    def embedding_id(self) -> str:
        identifier: str = self.embedding_import["model"]["id"]
        return identifier


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def models_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ModelsApi]:
    """The S5-05 app on the real binary: fresh database, real manager, both
    dev models imported through the route. Module-scoped so the expensive
    imports happen once; every child is stopped on the way out."""
    database = initialize_database(tmp_path_factory.mktemp("models-api"))
    manager = ModelRuntimeManager(MODEL_ENV.server)
    client = TestClient(
        create_app(
            Settings(session_token=VALID_TOKEN, llama_server=MODEL_ENV.server),
            database=database,
            model_runtime=manager,
        )
    )
    headers = {"Authorization": f"Bearer {VALID_TOKEN}"}
    try:
        generation = client.post(
            "/models/import",
            json={"path": str(MODEL_ENV.generation), "role": "generation"},
            headers=headers,
        )
        assert generation.status_code == 201, generation.text
        embedding = client.post(
            "/models/import",
            json={"path": str(MODEL_ENV.embedding), "role": "embedding"},
            headers=headers,
        )
        assert embedding.status_code == 201, embedding.text
        yield _ModelsApi(client, manager, headers, generation.json(), embedding.json())
    finally:
        # No llama-server child may outlive the module, whatever a test
        # left running on the way out.
        manager.stop_all()
        client.close()
        database.dispose()


def test_integration_imported_gguf_starts_via_provider(models_api: _ModelsApi) -> None:
    """The full S5-05 round trip on the real dev binary: both dev models
    import through the route with structural-only responses (exact byte
    sizes, hashed content, never the machine-local path -- T-10), the
    generation model starts under the manager, answers its real
    capability health check, occupies its role in ``GET /models``, and
    stops cleanly."""
    gen_body = models_api.generation_import
    gen_model = gen_body["model"]
    assert gen_body["duplicate"] is False
    assert gen_body["provenance"] == "unverified_import"
    assert gen_body["warning"] == IMPORT_PROVENANCE_WARNING
    assert gen_model["display_name"] == MODEL_ENV.generation.name
    assert re.fullmatch(r"[0-9a-f]{64}", gen_model["sha256"]) is not None
    assert gen_model["size_bytes"] == MODEL_ENV.generation.stat().st_size
    assert gen_model["embedding_dimensions"] is None
    assert "path" not in gen_model

    emb_body = models_api.embedding_import
    emb_model = emb_body["model"]
    assert emb_body["duplicate"] is False
    assert emb_model["size_bytes"] == MODEL_ENV.embedding.stat().st_size
    # Real-file hashing proven exactly on the (small) embedding model.
    assert emb_model["sha256"] == _sha256_of(MODEL_ENV.embedding)
    assert "path" not in emb_model

    model_id = models_api.generation_id
    started = models_api.client.post(
        f"/models/{model_id}/start", json={}, headers=models_api.headers
    )
    assert started.status_code == 200, started.text
    assert started.json()["state"] == "ready"
    assert started.json()["model_id"] == model_id

    inventory = models_api.client.get("/models", headers=models_api.headers)
    assert inventory.status_code == 200
    runtime = inventory.json()["runtime"]
    assert runtime["generation"]["state"] == "ready"
    assert runtime["generation"]["model_id"] == model_id
    assert runtime["embedding"]["state"] == "stopped"
    assert runtime["embedding"]["model_id"] is None
    listed = inventory.json()["models"]
    assert {m["id"] for m in listed} == {model_id, models_api.embedding_id}
    assert all("path" not in m for m in listed)

    health = models_api.client.post(f"/models/{model_id}/health", headers=models_api.headers)
    assert health.status_code == 200
    assert health.json()["state"] == "ready"
    assert health.json()["detail_code"] is None

    stopped = models_api.client.post(f"/models/{model_id}/stop", headers=models_api.headers)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["model_id"] is None

    after_stop = models_api.client.post(f"/models/{model_id}/health", headers=models_api.headers)
    assert after_stop.status_code == 200
    assert after_stop.json()["state"] == "stopped"
    assert after_stop.json()["model_id"] is None


def test_integration_import_refuses_truncated_file(
    models_api: _ModelsApi, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A truncated-but-well-formed GGUF copy passes the import head sniff
    (the sniff cannot see a missing tail) but fails closed at start: 502
    ``start_failed`` from the real child dying mid-load, and the role is
    left EMPTY -- never a half-started model pretending to serve."""
    truncated = tmp_path_factory.mktemp("truncated-model") / "truncated-embedding.gguf"
    with MODEL_ENV.embedding.open("rb") as src, truncated.open("wb") as dst:
        dst.write(src.read(1024 * 1024))  # valid head; tensors/tail missing
    assert truncated.stat().st_size >= MIN_MODEL_FILE_BYTES

    imported = models_api.client.post(
        "/models/import",
        json={
            "path": str(truncated),
            "role": "embedding",
            "display_name": "truncated-embedding-copy",
        },
        headers=models_api.headers,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["model"]["display_name"] == "truncated-embedding-copy"
    truncated_id: str = imported.json()["model"]["id"]
    assert truncated_id != models_api.embedding_id

    started = models_api.client.post(
        f"/models/{truncated_id}/start", json={}, headers=models_api.headers
    )
    assert started.status_code == 502, started.text
    assert started.json()["error"]["code"] == "start_failed"

    # Fail closed: the role stayed empty, so the truncated model reports
    # ``stopped`` without being contacted (it is not the occupant).
    after = models_api.client.post(f"/models/{truncated_id}/health", headers=models_api.headers)
    assert after.status_code == 200
    assert after.json()["state"] == "stopped"
    assert after.json()["model_id"] is None


def test_integration_dimension_pinned_to_record(models_api: _ModelsApi) -> None:
    """The S5-04 learn-and-pin path, end to end through the routes: the
    embedding capability's first real embed (its health check) pins the
    observed dimensionality on the persisted record, ``GET /models/{id}``
    reports it, and the NEXT start builds its config from the pinned
    record -- ``expected_dimensions`` 768, which a drift would violate
    inside ``embed()`` (S5-04)."""
    model_id = models_api.embedding_id
    started = models_api.client.post(
        f"/models/{model_id}/start", json={}, headers=models_api.headers
    )
    assert started.status_code == 200, started.text

    # The embedding health check IS a real embed (D-023): it fires the
    # manager's learn-and-pin callback into the database.
    health = models_api.client.post(f"/models/{model_id}/health", headers=models_api.headers)
    assert health.status_code == 200
    assert health.json()["state"] == "ready"

    record = models_api.client.get(f"/models/{model_id}", headers=models_api.headers)
    assert record.status_code == 200
    assert record.json()["embedding_dimensions"] == EXPECTED_EMBEDDING_DIMENSIONS

    models_api.client.post(f"/models/{model_id}/stop", headers=models_api.headers)
    restarted = models_api.client.post(
        f"/models/{model_id}/start", json={}, headers=models_api.headers
    )
    assert restarted.status_code == 200, restarted.text

    provider = models_api.manager.get("embedding")
    assert provider is not None
    config = provider._config
    assert isinstance(config, EmbeddingServerConfig)
    # The pinned record value (not None) is the next start's expectation.
    assert config.expected_dimensions == EXPECTED_EMBEDDING_DIMENSIONS
    vector = provider.embed("post-restart dimension pin probe")
    assert len(vector) == EXPECTED_EMBEDDING_DIMENSIONS

    models_api.client.post(f"/models/{model_id}/stop", headers=models_api.headers)
