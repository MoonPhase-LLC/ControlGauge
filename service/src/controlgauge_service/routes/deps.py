"""Shared route dependencies."""

from __future__ import annotations

from starlette.requests import Request

from ..db import Database
from ..model_runtime.download import DownloadManager
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


def get_downloads(request: Request) -> DownloadManager:
    """Sprint 6 download routes depend on the download manager; absent
    (or not yet wired) means the capability is unavailable, fail closed
    (mirrors `get_model_runtime`)."""
    manager: DownloadManager | None = getattr(request.app.state, "downloads", None)
    if manager is None:
        raise ApiError(503, "download_unavailable", "The download service is not available.")
    return manager
