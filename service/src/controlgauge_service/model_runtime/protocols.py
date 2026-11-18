"""Capability protocols for the model runtime (S5-02, D-011/D-023).

``GenerationCapable`` and ``EmbeddingCapable`` are the only interfaces the
AI pipeline may depend on (``docs/MODEL_RUNTIME.md`` §1) -- never a
concrete provider. Methods are synchronous; blocking calls run through
``run_in_threadpool`` in routes (the ``routes/artifacts.py`` pattern).
Implementations raise ``ModelRuntimeError`` instead of returning error
values: each ``ModelRuntimeError.code`` maps 1:1 to the documented error
union.

The data types here carry **structural metadata only** (token/timing
counts for baselines, capability descriptions) -- never model paths, the
model-server credential, or request/response content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

State = Literal["stopped", "starting", "ready", "unhealthy"]
Role = Literal["generation", "embedding"]


@dataclass(frozen=True)
class HealthStatus:
    """Result of a capability-specific real health check (D-023).

    ``detail_code`` is a ``MODEL_RUNTIME_ERROR_CODES`` member when
    ``state`` is ``"unhealthy"``; ``checked_at`` is set only when an
    actual check ran (never for the manager's ``"stopped"`` answer, where
    nothing was contacted)."""

    state: State
    detail_code: str | None = None
    checked_at: datetime | None = None


@dataclass(frozen=True)
class ModelDescriptor:
    """What a running model is, in closed form. ``runtime_version`` comes
    from the authenticated ``/props`` "build" information -- provider
    implementations must never copy the model path that ``/props`` also
    contains."""

    model_id: str
    display_name: str
    role: Role
    context_tokens: int
    runtime_version: str | None = None
    embedding_dimensions: int | None = None


@dataclass(frozen=True)
class GenerationUsage:
    """Structural metadata only (baselines); no content."""

    prompt_tokens: int | None
    output_tokens: int | None
    prompt_ms: float | None
    predicted_ms: float | None


@dataclass(frozen=True)
class StructuredGeneration:
    """Schema-validated JSON object plus its usage metadata. Deterministic
    validation (``AI_PIPELINE.md`` §8 shape) has already been applied by
    the time a caller sees one of these."""

    data: dict[str, Any]
    usage: GenerationUsage | None


@runtime_checkable
class GenerationCapable(Protocol):
    def generate(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        timeout_s: float,
        system_prompt: str | None = None,
    ) -> StructuredGeneration: ...

    def health_check(self) -> HealthStatus: ...

    def describe(self) -> ModelDescriptor: ...


@runtime_checkable
class EmbeddingCapable(Protocol):
    def embed(self, text: str) -> list[float]: ...

    def health_check(self) -> HealthStatus: ...

    def describe(self) -> ModelDescriptor: ...
