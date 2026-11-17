"""``LlamaCppProvider``: one supervised llama-server per capability
(S5-02, PD-024/D-017).

Lifecycle: ``start()`` allocates an OS port, spawns the pinned
``llama-server`` binary (credential via the child-scoped
``LLAMA_API_KEY`` env var, never argv) and polls authenticated
``GET /health`` until it is ready; ``stop()`` is a bounded, idempotent
termination. Inference calls raise ``ModelRuntimeError`` with fixed
codes -- never leak a model path, port, the credential, or response
content (T-10).

Privacy invariants enforced here:

- The HTTP client is loopback-only **and** built with
  ``trust_env=False`` plus a zero-retry transport: proxy environment
  variables can never route a loopback call to an external proxy
  (Sprint 5 risk register #8), and a connection is never silently
  redialed.
- The credential is generated fresh per provider (per launch, D-017),
  held in this module only, sent as ``Authorization: Bearer`` on every
  request including readiness polling, never logged, never returned.
- ``generate()`` validates every response against the caller's schema
  deterministically (``validation.validate_structured_output``) before
  it leaves this module, and persists nothing (Sprint 5 writes no
  analysis tables).
- ``embed()`` never truncates: over-long input is refused, and vectors
  are checked for finiteness and (once pinned) dimensionality.

``health_check()`` is capability-specific (D-023): a bounded real
generation/embedding call -- a hung child that accepts connections but
never answers is reported unhealthy, never trusted as ready.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .args import BIND_HOST, build_child_env, build_server_args
from .childproc import ChildHandle, spawn_child
from .config import (
    DEFAULT_EMBED_TIMEOUT_S,
    DEFAULT_STOP_GRACE_S,
    READINESS_POLL_INTERVAL_S,
    START_PORT_ATTEMPTS,
    EmbeddingServerConfig,
    GenerationServerConfig,
)
from .errors import ModelRuntimeError
from .portalloc import allocate_loopback_port
from .protocols import (
    GenerationUsage,
    HealthStatus,
    ModelDescriptor,
    Role,
    StructuredGeneration,
)
from .validation import validate_structured_output

logger = logging.getLogger("controlgauge_service.model_runtime")

#: Per-request ceiling while polling readiness -- a hung child must not
#: stall each poll for the whole start budget.
_READINESS_REQUEST_TIMEOUT_S = 5.0

#: The bounded real generation behind every generation health check
#: (D-023): fixed prompt, fixed tiny schema, fixed budget.
_HEALTH_PROMPT = 'Reply with the JSON object {"ok": true}.'
_HEALTH_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}
_EMBED_HEALTH_TEXT = "controlgauge health check"

_START_REASONS_THAT_RETRY = frozenset({"start_failed", "auth_rejected"})


@dataclass(frozen=True)
class _Readiness:
    """Why the readiness loop ended: ``ready`` on success, otherwise the
    error code (and the child exit code when it exited)."""

    ready: bool
    code: str
    exit_code: int | None = None


def _make_client(port: int, credential: str) -> httpx.Client:
    """Loopback-only client for one child. ``trust_env=False`` is a
    privacy requirement (proxy env vars must never route loopback calls
    externally); ``retries=0`` keeps failure semantics immediate and
    honest. The Authorization header rides every request, readiness
    polling included -- harmless on exempt endpoints, required on the
    rest (auth matrix recorded in ``docs/DEV_MODELS.md``)."""
    return httpx.Client(
        base_url=f"http://{BIND_HOST}:{port}",
        trust_env=False,
        timeout=DEFAULT_EMBED_TIMEOUT_S,
        headers={"Authorization": f"Bearer {credential}"},
        transport=httpx.HTTPTransport(retries=0),
    )


class LlamaCppProvider:
    """Runs one llama-server child and exposes it as a
    ``GenerationCapable`` and/or ``EmbeddingCapable`` provider, by role."""

    def __init__(
        self,
        config: GenerationServerConfig | EmbeddingServerConfig,
        runtime_binary: Path,
        *,
        role: Role,
        on_dimension_learned: Callable[[int], None] | None = None,
    ) -> None:
        self._config = config
        self._runtime_binary = runtime_binary
        self._role = role
        self._on_dimension_learned = on_dimension_learned
        #: Fresh per-launch credential (D-017): one provider instance is
        #: one launch; the manager constructs a new provider per start.
        self._credential = secrets.token_urlsafe(32)
        self._handle: ChildHandle | None = None
        self._client: httpx.Client | None = None
        self._port: int | None = None  # bound to the live child; never exposed
        self._stopped = False
        self._dimension_learned = False
        self._observed_dimensions: int | None = None

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Spawn the child and wait synchronously for readiness. Raises
        ``ModelRuntimeError`` (fail closed) with no partial state left
        behind."""
        if not self._runtime_binary.is_file():
            raise ModelRuntimeError("runtime_binary_missing")
        if not self._config.model_path.is_file():
            raise ModelRuntimeError("model_file_missing")
        env = build_child_env(self._credential)
        last_code = "start_failed"
        for _attempt in range(START_PORT_ATTEMPTS):
            port = allocate_loopback_port()
            argv = [str(self._runtime_binary), *build_server_args(self._config, port)]
            try:
                handle = spawn_child(argv, env)
            except OSError:
                # Spawn failure (binary not executable, ...): structural
                # exit code only -- never the argv or the path (T-10).
                logger.warning("model_server_spawn_failed", extra={"role": self._role})
                raise ModelRuntimeError("start_failed") from None
            self._handle = handle
            client = _make_client(port, self._credential)
            readiness = self._await_ready(client, handle)
            if readiness.ready:
                self._client = client
                self._port = port
                self._stopped = False
                logger.info(
                    "model_server_ready", extra={"role": self._role, "attempt": _attempt + 1}
                )
                return
            client.close()
            handle.terminate(DEFAULT_STOP_GRACE_S)
            self._handle = None
            last_code = readiness.code
            if readiness.code == "start_failed":
                logger.warning(
                    "model_server_start_failed",
                    extra={"role": self._role, "exit_code": readiness.exit_code},
                )
            if last_code not in _START_REASONS_THAT_RETRY:
                break
        raise ModelRuntimeError(last_code)

    def _await_ready(self, client: httpx.Client, handle: ChildHandle) -> _Readiness:
        deadline = time.monotonic() + self._config.start_timeout_s
        while time.monotonic() < deadline:
            exit_code = handle.poll()
            if exit_code is not None:
                return _Readiness(False, "start_failed", exit_code)
            try:
                response = client.get("/health", timeout=_READINESS_REQUEST_TIMEOUT_S)
            except httpx.TimeoutException:
                pass  # keep polling until the deadline
            except httpx.TransportError:
                pass  # not listening yet -- normal before the bind
            else:
                if response.status_code == 200:
                    return _Readiness(True, "ready")
                if response.status_code == 401:
                    # Our own credential rejected: either it never reached
                    # the child or an impostor holds the port (TOCTOU) --
                    # neither is fixed by waiting.
                    return _Readiness(False, "auth_rejected")
            time.sleep(READINESS_POLL_INTERVAL_S)
        return _Readiness(False, "start_timeout")

    def stop(self) -> None:
        """Idempotent shutdown: bounded terminate, then resource release.
        In-flight calls fail cleanly (closed client -> ``transport_error``;
        post-stop calls -> ``not_loaded``)."""
        self._stopped = True
        client, self._client = self._client, None
        handle, self._handle = self._handle, None
        self._port = None
        if client is not None:
            client.close()
        if handle is not None:
            handle.terminate(DEFAULT_STOP_GRACE_S)
            handle.close()

    # --- generation -----------------------------------------------------------

    def generate(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        timeout_s: float,
        system_prompt: str | None = None,
    ) -> StructuredGeneration:
        """One schema-constrained generation. The caller's schema rides
        ``response_format`` (sampler enforcement); the parsed answer is
        then validated deterministically (B2) before return. Nothing is
        persisted."""
        client = self._usable_client()
        self._require_live_child()
        config = self._config
        if not isinstance(config, GenerationServerConfig):  # unreachable: role/config are paired
            raise RuntimeError("generation_provider_misassembled")
        payload: dict[str, Any] = {
            "messages": (
                ([{"role": "system", "content": system_prompt}] if system_prompt else [])
                + [{"role": "user", "content": prompt}]
            ),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": dict(schema)},
            },
            "max_tokens": config.max_output_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "top_k": config.top_k,
            "stream": False,
        }
        body = self._request_json(client, "POST", "/v1/chat/completions", payload, timeout_s)
        content = self._choice_content(body)
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ModelRuntimeError("invalid_output") from exc
        if not isinstance(data, dict):
            raise ModelRuntimeError("invalid_output")
        validate_structured_output(data, schema)
        return StructuredGeneration(data=data, usage=self._usage(body))

    # --- embedding ------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """One embedding of ``text``. Never truncates silently; checks
        finiteness and, once a dimension is pinned, dimensionality.
        Learns and reports the dimension on first success (S5-04)."""
        client = self._usable_client()
        self._require_live_child()
        config = self._config
        if not isinstance(config, EmbeddingServerConfig):  # unreachable: role/config are paired
            raise RuntimeError("embedding_provider_misassembled")
        if len(text) > config.max_input_chars:
            raise ModelRuntimeError("input_too_long")
        body = self._request_json(
            client, "POST", "/v1/embeddings", {"input": text}, DEFAULT_EMBED_TIMEOUT_S
        )
        vector = self._embedding_vector(body)
        self._learn_dimension(len(vector))
        return vector

    def _learn_dimension(self, dimensions: int) -> None:
        config = self._config
        expected = config.expected_dimensions if isinstance(config, EmbeddingServerConfig) else None
        if expected is not None and dimensions != expected:
            raise ModelRuntimeError("embedding_dimension_mismatch")
        first_learning = self._observed_dimensions is None
        if self._observed_dimensions is not None and self._observed_dimensions != dimensions:
            raise ModelRuntimeError("embedding_dimension_mismatch")
        self._observed_dimensions = dimensions
        if first_learning and not self._dimension_learned:
            self._dimension_learned = True
            if self._on_dimension_learned is not None:
                self._on_dimension_learned(dimensions)

    # --- health / describe ----------------------------------------------------

    def health_check(self) -> HealthStatus:
        """A bounded REAL capability call (D-023) -- never a liveness-only
        trust. Failure of any step means ``unhealthy`` with the fixed
        detail code; nothing is persisted."""
        try:
            if self._role == "generation":
                generation = self.generate(
                    _HEALTH_PROMPT,
                    _HEALTH_SCHEMA,
                    timeout_s=self._config.health_timeout_s,
                )
                if generation.data.get("ok") is not True:
                    return self._unhealthy("invalid_output")
            else:
                # D-023: a minimal embed() on a fixed short string --
                # finiteness and dimensionality are enforced inside
                # embed() itself and surface as unhealthy here.
                self.embed(_EMBED_HEALTH_TEXT)
        except ModelRuntimeError as exc:
            return self._unhealthy(exc.code)
        return HealthStatus(state="ready", detail_code=None, checked_at=datetime.now(UTC))

    def describe(self) -> ModelDescriptor:
        """Closed description from an authenticated ``GET /props``. The
        props payload contains the model path -- it is never copied
        here; only build/version metadata is extracted. Props
        unavailable -> ``runtime_version=None``."""
        runtime_version: str | None = None
        client = self._client
        if client is not None and not self._stopped:
            try:
                body = self._request_json(client, "GET", "/props", None, DEFAULT_EMBED_TIMEOUT_S)
            except ModelRuntimeError:
                runtime_version = None
            else:
                runtime_version = _extract_runtime_version(body)
        return ModelDescriptor(
            model_id=self._config.model_id,
            display_name=self._config.model_id,
            role=self._role,
            context_tokens=self._config.context_tokens,
            runtime_version=runtime_version,
            embedding_dimensions=self._observed_dimensions if self._role == "embedding" else None,
        )

    # --- request plumbing -----------------------------------------------------

    def _usable_client(self) -> httpx.Client:
        if self._stopped or self._client is None:
            raise ModelRuntimeError("not_loaded")
        return self._client

    def _require_live_child(self) -> None:
        handle = self._handle
        if handle is not None and handle.poll() is not None:
            raise ModelRuntimeError("child_exited")

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        try:
            if payload is None:
                response = client.request(method, path, timeout=timeout_s)
            else:
                response = client.request(method, path, json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ModelRuntimeError("request_timeout") from exc
        except httpx.TransportError as exc:
            raise ModelRuntimeError("transport_error") from exc
        if response.status_code == 401:
            raise ModelRuntimeError("auth_rejected")
        if response.status_code == 400:
            raise ModelRuntimeError("invalid_request")
        if response.status_code >= 500:
            raise ModelRuntimeError("server_error")
        if response.status_code < 200 or response.status_code >= 300:
            # 404/405/415...: a request this server will not serve.
            raise ModelRuntimeError("invalid_request")
        try:
            body = response.json()
        except ValueError as exc:
            raise ModelRuntimeError("invalid_output") from exc
        if not isinstance(body, dict):
            raise ModelRuntimeError("invalid_output")
        return body

    @staticmethod
    def _choice_content(body: dict[str, Any]) -> str:
        try:
            choices = body["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelRuntimeError("invalid_output") from exc
        if not isinstance(content, str):
            raise ModelRuntimeError("invalid_output")
        return content

    @staticmethod
    def _usage(body: dict[str, Any]) -> GenerationUsage | None:
        """Structural metadata only (baselines, S5-07); absent fields stay
        ``None``. Never includes content."""
        usage = body.get("usage")
        timings = body.get("timings")
        if not isinstance(usage, dict) and not isinstance(timings, dict):
            return None
        usage = usage if isinstance(usage, dict) else {}
        timings = timings if isinstance(timings, dict) else {}
        return GenerationUsage(
            prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            prompt_ms=_float_or_none(timings.get("prompt_ms")),
            predicted_ms=_float_or_none(timings.get("predicted_ms")),
        )

    @staticmethod
    def _embedding_vector(body: dict[str, Any]) -> list[float]:
        try:
            data = body["data"]
            embedding = data[0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelRuntimeError("invalid_output") from exc
        if not isinstance(embedding, list) or not all(
            isinstance(value, int | float) and not isinstance(value, bool) for value in embedding
        ):
            raise ModelRuntimeError("invalid_output")
        if not all(math.isfinite(float(value)) for value in embedding):
            raise ModelRuntimeError("non_finite_embedding")
        return [float(value) for value in embedding]

    def _unhealthy(self, code: str) -> HealthStatus:
        return HealthStatus(state="unhealthy", detail_code=code, checked_at=datetime.now(UTC))


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _extract_runtime_version(props: dict[str, Any]) -> str | None:
    """Only build/version metadata, tightly bounded -- never the model
    path or anything else /props carries."""
    build_info = props.get("build_info")
    if isinstance(build_info, str) and build_info:
        # llama-server b12480 serves e.g. "b12480-7f106b545".
        return build_info[:64]
    if isinstance(build_info, dict):
        parts: list[str] = []
        number = build_info.get("build_number")
        if isinstance(number, int) and not isinstance(number, bool):
            parts.append(f"b{number}")
        commit = build_info.get("commit")
        if isinstance(commit, str) and commit:
            parts.append(commit[:64])
        return " ".join(parts) if parts else None
    return None
