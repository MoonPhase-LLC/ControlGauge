"""ControlGauge local analysis service.

Exports resolve lazily so that light entry points -- notably the contained
parser worker (`evidence/worker.py`, Sprint 4), which must start quickly
and import as little as possible -- do not load FastAPI or SQLAlchemy just
by importing this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import create_app
    from .main import main

__all__ = ["create_app", "main"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "main":
        from .main import main

        return main
    raise AttributeError(name)
