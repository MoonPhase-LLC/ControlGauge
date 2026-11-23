"""``ExternalEndpointProvider``: a user-registered LOOPBACK
OpenAI-compatible local inference server as a managed provider (S6-05).

Unlike ``LlamaCppProvider`` this provider spawns NOTHING: the server is
already running and is not ours to start or kill. ``stop()`` is
idempotent client disposal only. The httpx client is loopback by
construction (the registration route refuses any non-loopback base URL,
fail-closed) and is built with ``trust_env=False`` plus a zero-retry
transport -- proxy environment variables can never route a loopback call
to an external proxy, and a connection is never silently redialed (the
Sprint 5 privacy invariants, restated).

No credentials are stored or sent (V0.1 registers unauthenticated
loopback endpoints only; an endpoint requiring Bearer auth fails health
with ``auth_rejected``). ``generate()`` sends a MINIMAL payload --
messages, the caller's schema as ``response_format``, ``stream:false``:
sampling configuration belongs to the user-managed endpoint, not to us.

Request parsing and error semantics are the shared module-level helpers
in ``provider.py`` -- one implementation of the closed error mapping,
never a drifting copy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import DEFAULT_EMBED_TIMEOUT_S
from .errors import ModelRuntimeError
from .protocols import (
    HealthStatus,
    ModelDescriptor,
    Role,
    StructuredGeneration,
)
from .provider import (
    EMBED_HEALTH_TEXT,
    HEALTH_PROMPT,
    HEALTH_SCHEMA,
    choice_content,
    embedding_vector,
    generation_usage,
    request_json,
    unhealthy_status,
)
from .validation import validate_structured_output

logger = logging.getLogger("controlgauge_service.model_runtime.external")

#: Health detail codes that mean the endpoint could not be reached (or
#: refused us) at all -- recorded as ``unreachable`` in the registry;
#: every other failure means it answered but misbehaved (``unhealthy``).
UNREACHABLE_CODES = frozenset({"transport_error", "request_timeout", "auth_rejected"})

#: Every request to an external endpoint is bounded. Health probes are
#: tighter (the registration/health routes promise a bounded probe);
#: generate/embed calls get the standard per-request ceiling. An endpoint
#: that accepts connections but never answers is ``request_timeout``,
#: never trusted as ready.
ENDPOINT_HEALTH_TIMEOUT_S = 10.0
ENDPOINT_REQUEST_TIMEOUT_S = DEFAULT_EMBED_TIMEOUT_S


def endpoint_health_state(failure_code: str | None) -> str:
    """Map a health check outcome onto the registry's recorded health
    state: ``None`` (the check passed) is ``ready``; a transport-level
    failure code is ``unreachable``; every other failure code means the
    endpoint answered but misbehaved (``unhealthy``)."""
    if failure_code is None:
        return "ready"
    if failure_code in UNREACHABLE_CODES:
        return "unreachable"
    return "unhealthy"


class ExternalEndpointProvider:
    """``GenerationCapable`` + ``EmbeddingCapable`` against a
    user-registered LOOPBACK OpenAI-compatible local server
    (llama-server, or any compatible endpoint)."""

    def __init__(
        self,
        endpoint_id: str,
        display_name: str,
        base_url: str,
        role: Role,
        *,
        backend: str,
    ) -> None:
        self._endpoint_id = endpoint_id
        self._display_name = display_name
        self._base_url = base_url  # normalized scheme://host:port (loopback-validated)
        self._role = role
        self._backend = backend  # recorded, never logged (T-10 discipline)
        self._client: httpx.Client | None = self._make_client(base_url)
        self._stopped = False
        self._observed_dimensions: int | None = None

    @staticmethod
    def _make_client(base_url: str) -> httpx.Client:
        """Loopback-only client: ``trust_env=False`` and ``retries=0``
        are privacy requirements (proxy env vars must never route
        loopback calls externally; failure semantics stay immediate and
        honest). No Authorization header -- nothing is stored to send."""
        return httpx.Client(
            base_url=base_url,
            trust_env=False,
            timeout=DEFAULT_EMBED_TIMEOUT_S,
            transport=httpx.HTTPTransport(retries=0),
        )

    # --- identity ------------------------------------------------------------

    @property
    def model_id(self) -> str:
        """The registry identity (the `external_endpoint` id), closed
        reporting -- never a URL or a model name."""
        return self._endpoint_id

    def describe(self) -> ModelDescriptor:
        """Closed description. The endpoint's context window and runtime
        version are unknown (we do not probe identity beyond the
        capability call itself): ``context_tokens=0`` marks that
        honestly, ``runtime_version`` stays ``None``."""
        return ModelDescriptor(
            model_id=self._endpoint_id,
            display_name=self._display_name,
            role=self._role,
            context_tokens=0,
            runtime_version=None,
            embedding_dimensions=self._observed_dimensions,
        )

    # --- generation -----------------------------------------------------------

    def generate(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        timeout_s: float,
        system_prompt: str | None = None,
    ) -> StructuredGeneration:
        """One schema-constrained generation. The payload is MINIMAL:
        sampling configuration belongs to the user-managed endpoint. The
        parsed answer is validated deterministically before it leaves
        this module; nothing is persisted."""
        client = self._usable_client()
        payload: dict[str, Any] = {
            "messages": (
                ([{"role": "system", "content": system_prompt}] if system_prompt else [])
                + [{"role": "user", "content": prompt}]
            ),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": dict(schema)},
            },
            "stream": False,
        }
        body = request_json(client, "POST", "/v1/chat/completions", payload, timeout_s)
        content = choice_content(body)
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ModelRuntimeError("invalid_output") from exc
        if not isinstance(data, dict):
            raise ModelRuntimeError("invalid_output")
        validate_structured_output(data, schema)
        return StructuredGeneration(data=data, usage=generation_usage(body))

    # --- embedding ------------------------------------------------------------

    def embed(self, text: str, *, timeout_s: float = ENDPOINT_REQUEST_TIMEOUT_S) -> list[float]:
        """One embedding of ``text``: finiteness is enforced by the
        shared parser; the learned dimensionality is pinned on first
        success and drift-checked afterwards."""
        client = self._usable_client()
        body = request_json(client, "POST", "/v1/embeddings", {"input": text}, timeout_s)
        vector = embedding_vector(body)
        if self._observed_dimensions is not None and self._observed_dimensions != len(vector):
            raise ModelRuntimeError("embedding_dimension_mismatch")
        self._observed_dimensions = len(vector)
        return vector

    # --- health / lifecycle ---------------------------------------------------

    def health_check(self) -> HealthStatus:
        """A bounded REAL capability call (D-023) -- the fixed health
        generation for a generation endpoint, the fixed minimal embed for
        an embedding endpoint. A hung endpoint that accepts connections
        but never answers is reported ``unhealthy`` (``request_timeout``),
        never trusted as ready."""
        try:
            if self._role == "generation":
                generation = self.generate(
                    HEALTH_PROMPT,
                    HEALTH_SCHEMA,
                    timeout_s=ENDPOINT_HEALTH_TIMEOUT_S,
                )
                if generation.data.get("ok") is not True:
                    return unhealthy_status("invalid_output")
            else:
                self.embed(EMBED_HEALTH_TEXT, timeout_s=ENDPOINT_HEALTH_TIMEOUT_S)
        except ModelRuntimeError as exc:
            return unhealthy_status(exc.code)
        return HealthStatus(state="ready", detail_code=None, checked_at=datetime.now(UTC))

    def stop(self) -> None:
        """Idempotent client disposal. The child is NOT ours to kill --
        the endpoint keeps running for its owner; in-flight calls fail
        cleanly on the closed client (``transport_error``)."""
        self._stopped = True
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def _usable_client(self) -> httpx.Client:
        if self._stopped or self._client is None:
            raise ModelRuntimeError("not_loaded")
        return self._client
