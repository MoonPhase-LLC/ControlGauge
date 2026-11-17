"""Shared route dependencies."""

from __future__ import annotations

from starlette.requests import Request

from ..db import Database
from ..model_runtime.manager import ModelRuntimeManager
from .errors import ApiError


def get_database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise ApiError(503, "database_unavailable", "The local database is not available.")
    return database


def get_model_runtime(request: Request) -> ModelRuntimeManager:
    """Sprint 5 model routes depend on the per-capability manager; absent
    (or not yet wired) means the capability is unavailable, fail closed
    (mirrors `get_database`)."""
    manager: ModelRuntimeManager | None = getattr(request.app.state, "model_runtime", None)
    if manager is None:
        raise ApiError(503, "model_runtime_unavailable", "The model runtime is not available.")
    return manager
