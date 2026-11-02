"""Shared route dependencies."""

from __future__ import annotations

from starlette.requests import Request

from ..db import Database
from .errors import ApiError


def get_database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise ApiError(503, "database_unavailable", "The local database is not available.")
    return database
