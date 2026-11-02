"""Local SQLite persistence (Sprint 2)."""

from __future__ import annotations

from .bootstrap import DatabaseInitError, initialize_database
from .engine import Database

__all__ = ["Database", "DatabaseInitError", "initialize_database"]
