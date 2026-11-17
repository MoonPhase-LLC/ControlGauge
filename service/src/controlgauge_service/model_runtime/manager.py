"""Per-capability model runtime manager (S5-02, D-011/D-023).

At most one provider per role (generation, embedding). Starting over an
occupied role is the documented **switch**: the current provider is
stopped first -- in-flight calls fail cleanly on their own closed
clients -- then the replacement starts; if the replacement fails to
start, the role stays **empty** (fail closed, never a silent keep-old).

A single ``threading.Lock`` serializes start/stop/status transitions;
``generate``/``embed`` never take it. ``stop_all`` is invoked next to
``ingestion.stop()`` in both process entry points (the established
shutdown pattern) so no model-server child outlives the service.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, TypeAlias

from .config import EmbeddingServerConfig, GenerationServerConfig
from .errors import ModelRuntimeError
from .protocols import HealthStatus, ModelDescriptor
from .provider import LlamaCppProvider

RoleLiteral: TypeAlias = Literal["generation", "embedding"]
_ROLES: tuple[RoleLiteral, ...] = ("generation", "embedding")


class ModelRuntimeManager:
    """Owns the active provider for each capability."""

    ROLE_LITERAL: TypeAlias = RoleLiteral

    def __init__(self, runtime_binary: Path | None) -> None:
        self._runtime_binary = runtime_binary
        self._providers: dict[RoleLiteral, LlamaCppProvider] = {}
        self._lock = threading.Lock()

    def start(
        self,
        role: RoleLiteral,
        config: GenerationServerConfig | EmbeddingServerConfig,
        *,
        display_name: str,
        on_dimension_learned: Callable[[int], None] | None = None,
    ) -> ModelDescriptor:
        """Stop the role's current provider (if any), start the
        replacement, and return its closed descriptor. Raises
        ``ModelRuntimeError``; on failure the role is left empty."""
        with self._lock:
            self._stop_locked(role)
            provider = LlamaCppProvider(
                config,
                runtime_binary=self._require_binary(),
                role=role,
                on_dimension_learned=on_dimension_learned,
            )
            try:
                provider.start()
            except ModelRuntimeError:
                # Fail closed: the role stays empty rather than silently
                # keeping the stopped previous model.
                raise
            self._providers[role] = provider
            descriptor = provider.describe()
            if descriptor.display_name != display_name:
                descriptor = replace(descriptor, display_name=display_name)
            return descriptor

    def stop(self, role: RoleLiteral) -> None:
        """Idempotent stop of one role's provider."""
        with self._lock:
            self._stop_locked(role)

    def get(self, role: RoleLiteral) -> LlamaCppProvider | None:
        """The active provider for the role, or ``None``."""
        with self._lock:
            return self._providers.get(role)

    def status(self, role: RoleLiteral) -> HealthStatus:
        """``stopped`` when absent; otherwise a real capability-specific
        health check -- never a dead-child trust (a stopped provider
        reports unhealthy with its detail code)."""
        with self._lock:
            provider = self._providers.get(role)
            if provider is None:
                return HealthStatus(state="stopped")
            return provider.health_check()

    def stop_all(self) -> None:
        """Stop every role (process shutdown)."""
        with self._lock:
            for role in _ROLES:
                self._stop_locked(role)

    # --- internals -------------------------------------------------------------

    def _require_binary(self) -> Path:
        if self._runtime_binary is None:
            raise ModelRuntimeError("runtime_binary_missing")
        return self._runtime_binary

    def _stop_locked(self, role: RoleLiteral) -> None:
        provider = self._providers.pop(role, None)
        if provider is not None:
            provider.stop()
